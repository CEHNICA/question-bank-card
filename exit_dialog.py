"""桌面退出确认：默认继续使用，仅明确确认后才允许关闭主窗口。"""

from __future__ import annotations

import threading


def build_exit_dialog(owner=None):
    """在宿主的 WinForms UI 线程构建弹窗；返回弹窗与两个按钮供隔离验收。"""
    import clr

    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System.Drawing import Color, Font, FontStyle, Point, Size
    from System.Windows.Forms import (
        AutoScaleMode,
        Button,
        DialogResult,
        FlatStyle,
        Form,
        FormBorderStyle,
        FormStartPosition,
        Label,
    )

    dialog = Form()
    dialog.Text = "退出题有据"
    dialog.AutoScaleMode = AutoScaleMode.Dpi
    dialog.ClientSize = Size(440, 196)
    dialog.FormBorderStyle = FormBorderStyle.FixedDialog
    dialog.MaximizeBox = False
    dialog.MinimizeBox = False
    dialog.ShowInTaskbar = owner is None
    dialog.StartPosition = FormStartPosition.CenterParent if owner is not None else FormStartPosition.CenterScreen
    dialog.BackColor = Color.White
    dialog.Font = Font("Microsoft YaHei UI", 10)
    if owner is not None:
        dialog.Icon = owner.Icon

    title = Label()
    title.Text = "是否要退出题有据？"
    title.Font = Font("Microsoft YaHei UI", 13, FontStyle.Bold)
    title.ForeColor = Color.FromArgb(23, 45, 70)
    title.Location = Point(24, 24)
    title.Size = Size(390, 32)
    body = Label()
    body.Text = "退出前，请确认已保存当前编辑内容。"
    body.ForeColor = Color.FromArgb(71, 85, 105)
    body.Location = Point(24, 68)
    body.Size = Size(390, 40)

    cancel = Button()
    cancel.Name = "continueUsing"
    cancel.Text = "继续使用"
    cancel.Location = Point(188, 132)
    cancel.Size = Size(108, 38)
    cancel.DialogResult = DialogResult.Cancel
    cancel.TabIndex = 0
    exit_button = Button()
    exit_button.Name = "confirmExit"
    exit_button.Text = "退出软件"
    exit_button.Location = Point(308, 132)
    exit_button.Size = Size(108, 38)
    exit_button.DialogResult = DialogResult.OK
    exit_button.TabIndex = 1
    exit_button.UseVisualStyleBackColor = False
    exit_button.FlatStyle = FlatStyle.Flat
    exit_button.BackColor = Color.White
    exit_button.ForeColor = Color.FromArgb(71, 85, 105)
    exit_button.FlatAppearance.BorderColor = Color.FromArgb(203, 213, 225)
    exit_button.FlatAppearance.MouseOverBackColor = Color.FromArgb(220, 38, 38)
    exit_button.FlatAppearance.MouseDownBackColor = Color.FromArgb(185, 28, 28)

    def enter(_sender, _event):
        exit_button.ForeColor = Color.White
        exit_button.FlatAppearance.BorderColor = Color.FromArgb(220, 38, 38)

    def leave(_sender, _event):
        exit_button.ForeColor = Color.FromArgb(71, 85, 105)
        exit_button.FlatAppearance.BorderColor = Color.FromArgb(203, 213, 225)

    exit_button.MouseEnter += enter
    exit_button.MouseLeave += leave
    dialog.Controls.AddRange([title, body, cancel, exit_button])
    dialog.AcceptButton = cancel
    dialog.CancelButton = cancel
    dialog.Shown += lambda _sender, _event: cancel.Focus()
    return dialog, cancel, exit_button


def confirm_exit(owner=None) -> bool:
    """弹窗异常时使用系统确认；两种确认都失败时保持软件打开。"""
    dialog = None
    try:
        dialog, _cancel, _exit = build_exit_dialog(owner)
        from System.Windows.Forms import DialogResult

        result = dialog.ShowDialog(owner) if owner is not None else dialog.ShowDialog()
        return result == DialogResult.OK
    except Exception:  # noqa: BLE001 - 不能因确认弹窗失败而直接退出
        try:
            import ctypes

            hwnd = int(owner.Handle.ToInt64()) if owner is not None else 0
            # YES/NO、问号、默认 NO；右上角关闭和 Esc 同样不会确认。
            return (
                ctypes.windll.user32.MessageBoxW(
                    hwnd, "是否要退出题有据？\n\n退出前，请确认已保存当前编辑内容。", "退出题有据", 0x04 | 0x20 | 0x100
                )
                == 6
            )
        except Exception:  # noqa: BLE001 - 无法获得明确确认时保留窗口
            return False
    finally:
        if dialog is not None:
            try:
                dialog.Dispose()
            except Exception:  # noqa: BLE001 - 释放弹窗异常不能绕过退出确认
                pass


class ExitConfirmation:
    """防止连续点击关闭按钮时重复弹窗。"""

    def __init__(self, window) -> None:
        self.window = window
        self._lock = threading.Lock()

    def __call__(self) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        try:
            return confirm_exit(getattr(self.window, "native", None))
        except Exception:  # noqa: BLE001 - 宿主回调异常也必须取消关闭
            return False
        finally:
            self._lock.release()

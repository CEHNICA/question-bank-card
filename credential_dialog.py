"""Native Windows dialog for first-run API setup and later reconfiguration.

The dialog deliberately knows nothing about the launcher implementation.  It
only normalizes the three supported credentials and stores them through the
existing DPAPI-backed :mod:`credential_store` module.  A caller may supply a
MinerU verifier; it is always run on a worker thread so a slow network cannot
freeze the window.
"""

from __future__ import annotations

import contextlib
import os
import queue
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from credential_store import CredentialStoreError, load_credentials, save_credentials


APP_TITLE = "题库题卡版"
Verifier = Callable[[str], bool | None]


def normalize_credential_values(values: dict[str, str]) -> dict[str, str]:
    """Normalize form values without logging or otherwise exposing them."""
    mineru = values.get("mineru_token", "").strip()
    if mineru.lower().startswith("bearer "):
        mineru = mineru[7:].strip()
    minimax = values.get("minimax_key", "").strip().replace("\\_", "_")
    siliconflow = values.get("siliconflow_key", "").strip()
    result = {"mineru_token": mineru, "minimax_key": minimax}
    if siliconflow:
        result["siliconflow_key"] = siliconflow
    return result


def validate_credential_values(values: dict[str, str]) -> str | None:
    """Return a user-facing error, or ``None`` when local checks pass."""
    if not values.get("mineru_token"):
        return "请输入 MinerU Token。"
    if not values.get("minimax_key"):
        return "请输入 MiniMax API Key。"
    for label, key in (
        ("MinerU Token", "mineru_token"),
        ("MiniMax API Key", "minimax_key"),
        ("硅基流动 API Key", "siliconflow_key"),
    ):
        value = values.get(key, "")
        if any(character.isspace() for character in value):
            return f"{label} 中不能包含空格或换行，请检查后重试。"
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            return f"{label} 中包含不可见字符，请重新粘贴。"
        if len(value) > 16_384:
            return f"{label} 过长，请检查是否粘贴了多余内容。"
    return None


def credentials_complete(values: dict[str, str]) -> bool:
    normalized = normalize_credential_values(values)
    return bool(normalized.get("mineru_token") and normalized.get("minimax_key"))


def _resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    return Path(frozen_root) if frozen_root else Path(__file__).resolve().parent


class CredentialDialog:
    """Small modal Tk window used by both first run and “配置 API”."""

    def __init__(
        self,
        *,
        first_run: bool = False,
        verify_mineru: Verifier | None = None,
        verify_minimax: Verifier | None = None,
        parent=None,
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.verify_mineru = verify_mineru
        self.verify_minimax = verify_minimax
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.saved = False
        self.busy = False
        self.owns_root = parent is None
        self.root = tk.Tk() if self.owns_root else tk.Toplevel(parent)
        self.root.title("首次设置 API" if first_run else "配置 API")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)
        self.root.configure(bg="#f5f5ef")
        self._set_icon()
        self._set_dpi()

        try:
            current = load_credentials()
            load_warning = ""
        except CredentialStoreError:
            # Saving a fresh payload safely replaces an unreadable old file.
            current = {}
            load_warning = "原来的配置无法读取，请重新填写后保存。"

        self.values = {
            "mineru_token": tk.StringVar(value=current.get("mineru_token", "")),
            "minimax_key": tk.StringVar(value=current.get("minimax_key", "")),
            "siliconflow_key": tk.StringVar(value=current.get("siliconflow_key", "")),
        }
        self.show_secrets = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value=load_warning)
        self._entries = []
        self._build(first_run)
        self._center()
        self.root.after(80, self._poll_events)

    def _set_dpi(self) -> None:
        if os.name != "nt":
            return
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)

    def _set_icon(self) -> None:
        icon = _resource_root() / "assets" / "app.ico"
        with contextlib.suppress(Exception):
            if icon.is_file():
                self.root.iconbitmap(default=str(icon))

    def _build(self, first_run: bool) -> None:
        tk, ttk = self.tk, self.ttk
        style = ttk.Style(self.root)
        with contextlib.suppress(Exception):
            style.theme_use("clam")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Title.TLabel", background="#ffffff", foreground="#183b34",
                        font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Body.TLabel", background="#ffffff", foreground="#55645f",
                        font=("Microsoft YaHei UI", 9))
        style.configure("Field.TLabel", background="#ffffff", foreground="#243c36",
                        font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Hint.TLabel", background="#ffffff", foreground="#77827e",
                        font=("Microsoft YaHei UI", 8))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 9, "bold"),
                        foreground="#ffffff", background="#1f6b5f", bordercolor="#1f6b5f")
        style.map("Accent.TButton", background=[("active", "#195a50"), ("disabled", "#9aaaa5")])

        outer = tk.Frame(self.root, bg="#f5f5ef", padx=18, pady=18)
        outer.pack(fill="both", expand=True)
        card = ttk.Frame(outer, style="Card.TFrame", padding=(26, 22))
        card.pack(fill="both", expand=True)

        title = "第一次使用，先配置 API" if first_run else "配置 API"
        ttk.Label(card, text=title, style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            card,
            text="用于解析试卷和核对题目。MinerU 与 MiniMax 必填，硅基流动可选。",
            style="Body.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 18))

        row = 2
        fields = (
            ("MinerU Token", "mineru_token", "必填 · 可直接粘贴 Bearer Token"),
            ("MiniMax API Key", "minimax_key", "必填 · 用于识读题目"),
            ("硅基流动 API Key", "siliconflow_key", "可选 · 使用另一家模型复核"),
        )
        for label, key, hint in fields:
            ttk.Label(card, text=label, style="Field.TLabel").grid(row=row, column=0, columnspan=2, sticky="w")
            row += 1
            entry = ttk.Entry(card, textvariable=self.values[key], show="●", width=64)
            entry.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(4, 3), ipady=5)
            entry.bind("<Return>", lambda _event: self._save())
            self._entries.append(entry)
            row += 1
            ttk.Label(card, text=hint, style="Hint.TLabel").grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 11))
            row += 1

        show = ttk.Checkbutton(card, text="显示输入内容", variable=self.show_secrets, command=self._toggle_visibility)
        show.grid(row=row, column=0, sticky="w", pady=(0, 13))
        row += 1

        notice = (
            "隐私说明：密钥通过 Windows DPAPI 加密，仅保存在当前 Windows 用户下，"
            "不会写入题库文件或日志。"
        )
        ttk.Label(card, text=notice, style="Body.TLabel", wraplength=520, justify="left").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )
        row += 1
        self.status_label = ttk.Label(card, textvariable=self.status, style="Body.TLabel",
                                      wraplength=520, justify="left")
        self.status_label.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 10))
        row += 1

        buttons = ttk.Frame(card, style="Card.TFrame")
        buttons.grid(row=row, column=0, columnspan=2, sticky="e")
        self.cancel_button = ttk.Button(buttons, text="取消", command=self._cancel)
        self.cancel_button.pack(side="left", padx=(0, 9))
        self.save_button = ttk.Button(buttons, text="保存并继续", style="Accent.TButton", command=self._save)
        self.save_button.pack(side="left")

        card.columnconfigure(0, weight=1)
        self._entries[0].focus_set()

    def _center(self) -> None:
        self.root.update_idletasks()
        width, height = 620, max(570, self.root.winfo_reqheight())
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(250, lambda: self.root.attributes("-topmost", False))

    def _toggle_visibility(self) -> None:
        mask = "" if self.show_secrets.get() else "●"
        for entry in self._entries:
            entry.configure(show=mask)

    def _form_values(self) -> dict[str, str]:
        return normalize_credential_values({key: value.get() for key, value in self.values.items()})

    def _set_busy(self, value: bool) -> None:
        self.busy = value
        state = "disabled" if value else "normal"
        self.save_button.configure(state=state)
        for entry in self._entries:
            entry.configure(state=state)

    def _save(self) -> None:
        if self.busy:
            return
        values = self._form_values()
        problem = validate_credential_values(values)
        if problem:
            self._show_error(problem)
            return
        if self.verify_mineru is None and self.verify_minimax is None:
            self._persist(values)
            return
        self._set_busy(True)
        self.status.set("正在验证 API 配置…")

        def verify() -> None:
            results: dict[str, bool | None] = {}
            for name, verifier, key in (
                ("MinerU", self.verify_mineru, "mineru_token"),
                ("MiniMax", self.verify_minimax, "minimax_key"),
            ):
                if verifier is None:
                    continue
                try:
                    results[name] = verifier(values[key])
                except Exception:  # noqa: BLE001 - a verifier failure is an unknown network result
                    results[name] = None
            self.events.put(("verified", (results, values)))

        threading.Thread(target=verify, name="credential-verification", daemon=True).start()

    def _persist(self, values: dict[str, str]) -> None:
        try:
            save_credentials(values)
        except CredentialStoreError as exc:
            self._set_busy(False)
            self._show_error(str(exc))
            return
        self.saved = True
        self.status.set("API 配置已加密保存。")
        self.root.after(180, self.root.destroy)

    def _verified(self, results: dict[str, bool | None], values: dict[str, str]) -> None:
        from tkinter import messagebox

        self._set_busy(False)
        invalid = [name for name, result in results.items() if result is False]
        if invalid:
            self._show_error(f"{'、'.join(invalid)} 的凭据未通过官网验证，请检查后重新输入。")
            return
        unknown = [name for name, result in results.items() if result is None]
        if unknown:
            proceed = messagebox.askyesno(
                "暂时无法验证",
                f"当前网络无法完成 {'、'.join(unknown)} 验证。可以先加密保存，实际上传时仍会由服务端校验。\n\n是否继续保存？",
                parent=self.root,
                default="no",
            )
            if not proceed:
                self.status.set("尚未保存，请检查网络或 Token 后重试。")
                return
        self._persist(values)

    def _show_error(self, text: str) -> None:
        from tkinter import messagebox

        self.status.set(text)
        messagebox.showerror("无法保存", text, parent=self.root)

    def _cancel(self) -> None:
        self.saved = False
        self.root.destroy()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "verified":
                    result, values = payload
                    self._verified(result, values)
        except queue.Empty:
            pass
        with contextlib.suppress(Exception):
            if self.root.winfo_exists():
                self.root.after(80, self._poll_events)

    def show(self) -> bool:
        if self.owns_root:
            self.root.mainloop()
        else:
            self.root.transient(self.root.master)
            self.root.grab_set()
            self.root.wait_window()
        return self.saved


def show_credential_dialog(
    *,
    first_run: bool = False,
    verify_mineru: Verifier | None = None,
    verify_minimax: Verifier | None = None,
    parent=None,
) -> bool:
    """Show the API setup window; return ``True`` only after a successful save."""
    return CredentialDialog(
        first_run=first_run,
        verify_mineru=verify_mineru,
        verify_minimax=verify_minimax,
        parent=parent,
    ).show()


__all__ = [
    "CredentialDialog",
    "credentials_complete",
    "normalize_credential_values",
    "show_credential_dialog",
    "validate_credential_values",
]

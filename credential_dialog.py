"""Native Windows dialog for first-run API and model-role configuration.

The dialog deliberately knows nothing about the launcher implementation.  It
normalizes the three supported credentials, stores them through the existing
DPAPI-backed store, and saves allow-listed non-secret model preferences beside
them.  A caller may supply API verifiers; they always run on a worker thread so
a slow network cannot freeze the window.
"""

from __future__ import annotations

import contextlib
import os
import queue
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from credential_store import (
    DEFAULT_MODEL_PREFERENCES,
    MAX_ACCOUNT_POOL_SIZE,
    CredentialStoreError,
    credential_pool,
    load_credentials,
    load_model_preferences,
    normalize_model_preferences,
    save_credentials,
    save_model_preferences,
)


APP_TITLE = "题有据"
Verifier = Callable[[str], bool | None]

MODEL_ROLE_OPTIONS = {
    "primary_engine": (
        ("MiniMax-M3", "minimax_m3"),
        ("Qwen3-VL-32B-Instruct（硅基流动）", "siliconflow_qwen3"),
    ),
    "checker_engine": (
        ("自动（优先选择另一家，缺少则跟随主读）", "auto"),
        ("MiniMax-M3", "minimax_m3"),
        ("Qwen3-VL-32B-Instruct（硅基流动）", "siliconflow_qwen3"),
    ),
    "arbiter_engine": (
        ("跟随主读", "primary"),
        ("跟随复核", "checker"),
        ("MiniMax-M3", "minimax_m3"),
        ("Qwen3-VL-32B-Instruct（硅基流动）", "siliconflow_qwen3"),
    ),
}
_MODEL_LABEL_BY_VALUE = {
    role: {value: label for label, value in options}
    for role, options in MODEL_ROLE_OPTIONS.items()
}
_MODEL_VALUE_BY_LABEL = {
    role: {label: value for label, value in options}
    for role, options in MODEL_ROLE_OPTIONS.items()
}


_FORM_POOLS = {
    "mineru": ("mineru_token", "mineru_tokens", "MinerU Token"),
    "minimax": ("minimax_key", "minimax_keys", "MiniMax API Key"),
    "siliconflow": ("siliconflow_key", "siliconflow_keys", "硅基流动 API Key"),
}


def _split_form_pool(value: str, service: str) -> list[str]:
    """Split the masked semicolon field while preserving invalid inner whitespace for validation."""

    accounts: list[str] = []
    for raw in value.split(";"):
        account = raw.strip()
        if service == "mineru" and account.lower().startswith("bearer "):
            account = account[7:].strip()
        if service == "minimax":
            account = account.replace("\\_", "_")
        if account and account not in accounts:
            accounts.append(account)
    return accounts


def _accounts(values: dict[str, object], service: str) -> list[str]:
    legacy_key, pool_key, _label = _FORM_POOLS[service]
    raw = values.get(pool_key) if pool_key in values else values.get(legacy_key, "")
    if isinstance(raw, str):
        return _split_form_pool(raw, service)
    if isinstance(raw, (list, tuple)):
        return [item for item in raw if isinstance(item, str) and item]
    return []


def normalize_credential_values(values: dict[str, str]) -> dict[str, object]:
    """Normalize three semicolon-separated account pools without exposing them."""

    result: dict[str, object] = {}
    for service, (legacy_key, pool_key, _label) in _FORM_POOLS.items():
        accounts = _split_form_pool(values.get(legacy_key, ""), service)
        if accounts:
            result[legacy_key] = accounts[0]
            result[pool_key] = accounts
    return result


def validate_credential_values(values: dict[str, object]) -> str | None:
    """Return a user-facing error, or ``None`` when local checks pass."""
    if not _accounts(values, "mineru"):
        return "请输入至少一个 MinerU Token。"
    for service, (_legacy_key, _pool_key, label) in _FORM_POOLS.items():
        accounts = _accounts(values, service)
        if len(accounts) > MAX_ACCOUNT_POOL_SIZE:
            return f"{label} 最多填写 {MAX_ACCOUNT_POOL_SIZE} 个账号。"
        for index, value in enumerate(accounts, start=1):
            prefix = f"{label} 的第 {index} 个账号"
            if any(character.isspace() for character in value):
                return f"{prefix}中不能包含空格或换行，请检查后重试。"
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                return f"{prefix}中包含不可见字符，请重新粘贴。"
            if len(value) > 16_384:
                return f"{prefix}过长，请检查是否粘贴了多余内容。"
    return None


def credentials_complete(
    values: dict[str, object], preferences: dict[str, str] | None = None,
) -> bool:
    selected = normalize_model_preferences(preferences or DEFAULT_MODEL_PREFERENCES)
    primary_service = "siliconflow" if selected["primary_engine"] == "siliconflow_qwen3" else "minimax"
    return bool(_accounts(values, "mineru") and _accounts(values, primary_service))


def validate_model_preferences(
    preferences: dict[str, str], credentials: dict[str, object],
) -> str | None:
    """Reject a role that explicitly requires an unavailable provider key."""
    normalized = normalize_model_preferences(preferences)
    explicit = {
        normalized["primary_engine"],
        normalized["checker_engine"],
        normalized["arbiter_engine"],
    }
    if not _accounts(credentials, "minimax") and "minimax_m3" in explicit:
        return "所选模型需要先填写 MiniMax API Key。"
    if not _accounts(credentials, "siliconflow") and "siliconflow_qwen3" in explicit:
        return "所选模型需要先填写硅基流动 API Key。"
    return None


def verify_credential_accounts(
    values: dict[str, object],
    *,
    verify_mineru: Verifier | None = None,
    verify_minimax: Verifier | None = None,
    verify_siliconflow: Verifier | None = None,
) -> dict[str, bool | None]:
    """Verify every configured account and return labels that contain no secret text."""

    results: dict[str, bool | None] = {}
    for name, verifier, service in (
        ("MinerU", verify_mineru, "mineru"),
        ("MiniMax", verify_minimax, "minimax"),
        ("硅基流动", verify_siliconflow, "siliconflow"),
    ):
        if verifier is None:
            continue
        for index, account in enumerate(_accounts(values, service), start=1):
            label = f"{name} 第 {index} 个账号"
            try:
                results[label] = verifier(account)
            except Exception:  # noqa: BLE001 - a verifier failure is an unknown network result
                results[label] = None
    return results


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
        verify_siliconflow: Verifier | None = None,
        parent=None,
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.verify_mineru = verify_mineru
        self.verify_minimax = verify_minimax
        self.verify_siliconflow = verify_siliconflow
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.saved = False
        self.busy = False
        self.owns_root = parent is None
        self.root = tk.Tk() if self.owns_root else tk.Toplevel(parent)
        dialog_title = "首次设置 API 与模型" if first_run else "配置 API 与模型"
        self.root.title(f"{APP_TITLE} · {dialog_title}")
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

        try:
            current_preferences = load_model_preferences()
        except CredentialStoreError:
            current_preferences = dict(DEFAULT_MODEL_PREFERENCES)
            preference_warning = "原来的模型选择无法读取，已恢复默认选择。"
            load_warning = "\n".join(filter(None, (load_warning, preference_warning)))

        self.values = {
            "mineru_token": tk.StringVar(value="; ".join(credential_pool(current, "mineru"))),
            "minimax_key": tk.StringVar(value="; ".join(credential_pool(current, "minimax"))),
            "siliconflow_key": tk.StringVar(value="; ".join(credential_pool(current, "siliconflow"))),
        }
        self.model_values = {
            role: tk.StringVar(value=_MODEL_LABEL_BY_VALUE[role][current_preferences[role]])
            for role in DEFAULT_MODEL_PREFERENCES
        }
        self.show_secrets = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value=load_warning)
        self._entries = []
        self._model_controls = []
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
        style.configure("Section.TLabel", background="#ffffff", foreground="#183b34",
                        font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 9, "bold"),
                        foreground="#ffffff", background="#1f6b5f", bordercolor="#1f6b5f")
        style.map("Accent.TButton", background=[("active", "#195a50"), ("disabled", "#9aaaa5")])

        outer = tk.Frame(self.root, bg="#f5f5ef", padx=18, pady=18)
        outer.pack(fill="both", expand=True)
        card = ttk.Frame(outer, style="Card.TFrame", padding=(26, 22))
        card.pack(fill="both", expand=True)

        title = "第一次使用，先配置 API 与模型" if first_run else "配置 API 与模型"
        ttk.Label(card, text=title, style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            card,
            text=("MinerU 必填；MiniMax 与硅基流动按下方所选模型填写。"
                  f"每类最多 {MAX_ACCOUNT_POOL_SIZE} 个账号，多个凭据用英文分号 ; 分隔。"),
            style="Body.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 18))

        row = 2
        fields = (
            ("MinerU Token（必填；多个用 ; 分隔）", "mineru_token"),
            ("MiniMax API Key（多个用 ; 分隔）", "minimax_key"),
            ("硅基流动 API Key（多个用 ; 分隔）", "siliconflow_key"),
        )
        for label, key in fields:
            ttk.Label(card, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w")
            entry = ttk.Entry(card, textvariable=self.values[key], show="●", width=64)
            entry.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=4, ipady=4)
            entry.bind("<Return>", lambda _event: self._save())
            self._entries.append(entry)
            row += 1

        show = ttk.Checkbutton(card, text="显示输入内容", variable=self.show_secrets, command=self._toggle_visibility)
        show.grid(row=row, column=1, sticky="w", padx=(12, 0), pady=(1, 13))
        row += 1

        ttk.Separator(card, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(0, 13)
        )
        row += 1
        ttk.Label(card, text="识读模型分工", style="Section.TLabel").grid(
            row=row, column=0, columnspan=2, sticky="w"
        )
        row += 1
        ttk.Label(
            card,
            text="主读先形成题面；复核独立核对；裁决只在两次读法不一致时决定最终文本。",
            style="Body.TLabel", wraplength=560, justify="left",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(3, 11))
        row += 1

        model_fields = (
            ("主读", "primary_engine"),
            ("复核", "checker_engine"),
            ("裁决", "arbiter_engine"),
        )
        for label, role in model_fields:
            ttk.Label(card, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w")
            combobox = ttk.Combobox(
                card,
                textvariable=self.model_values[role],
                values=[option_label for option_label, _value in MODEL_ROLE_OPTIONS[role]],
                state="readonly",
                width=48,
            )
            combobox.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=(1, 1))
            combobox.bind("<Return>", lambda _event: self._save())
            self._model_controls.append(combobox)
            row += 1

        ttk.Label(
            card,
            text="这些选择只影响之后识读；已有题卡和正式题库不会自动变化。",
            style="Body.TLabel", wraplength=560, justify="left",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(2, 12))
        row += 1

        notice = (
            "隐私说明：密钥通过 Windows DPAPI 加密，仅保存在当前 Windows 用户下，"
            "不会写入题库文件或日志。"
        )
        ttk.Label(card, text=notice, style="Body.TLabel", wraplength=560, justify="left").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )
        row += 1
        self.status_label = ttk.Label(card, textvariable=self.status, style="Body.TLabel",
                                      wraplength=560, justify="left")
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
        width = max(690, self.root.winfo_reqwidth())
        height = max(620, self.root.winfo_reqheight())
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

    def _form_values(self) -> dict[str, object]:
        return normalize_credential_values({key: value.get() for key, value in self.values.items()})

    def _form_preferences(self) -> dict[str, str]:
        selected = {
            role: _MODEL_VALUE_BY_LABEL[role].get(variable.get(), DEFAULT_MODEL_PREFERENCES[role])
            for role, variable in self.model_values.items()
        }
        return normalize_model_preferences(selected)

    def _set_busy(self, value: bool) -> None:
        self.busy = value
        state = "disabled" if value else "normal"
        self.save_button.configure(state=state)
        for entry in self._entries:
            entry.configure(state=state)
        for control in self._model_controls:
            control.configure(state="disabled" if value else "readonly")

    def _save(self) -> None:
        if self.busy:
            return
        values = self._form_values()
        preferences = self._form_preferences()
        problem = validate_credential_values(values)
        if problem is None:
            problem = validate_model_preferences(preferences, values)
        if problem:
            self._show_error(problem)
            return
        if all(verifier is None for verifier in (
            self.verify_mineru, self.verify_minimax, self.verify_siliconflow,
        )):
            self._persist(values, preferences)
            return
        self._set_busy(True)
        # 目前只有 MinerU 提供不产生识读费用的凭据预检。
        # 模型 Key 不冒充“已验证”，会在首次识读时由账号池隔离失效项。
        if self.verify_mineru and not self.verify_minimax and not self.verify_siliconflow:
            self.status.set("正在验证 MinerU Token…")
        else:
            self.status.set("正在验证 API 配置…")

        def verify() -> None:
            results = verify_credential_accounts(
                values,
                verify_mineru=self.verify_mineru,
                verify_minimax=self.verify_minimax,
                verify_siliconflow=self.verify_siliconflow,
            )
            self.events.put(("verified", (results, values, preferences)))

        threading.Thread(target=verify, name="credential-verification", daemon=True).start()

    def _persist(self, values: dict[str, object], preferences: dict[str, str]) -> None:
        try:
            save_credentials(values)
            save_model_preferences(preferences)
        except CredentialStoreError as exc:
            self._set_busy(False)
            self._show_error(str(exc))
            return
        self.saved = True
        self.status.set("API 配置与模型选择已保存。")
        self.root.after(180, self.root.destroy)

    def _verified(
        self,
        results: dict[str, bool | None],
        values: dict[str, object],
        preferences: dict[str, str],
    ) -> None:
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
        self._persist(values, preferences)

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
                    result, values, preferences = payload
                    self._verified(result, values, preferences)
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
    verify_siliconflow: Verifier | None = None,
    parent=None,
) -> bool:
    """Show the API setup window; return ``True`` only after a successful save."""
    return CredentialDialog(
        first_run=first_run,
        verify_mineru=verify_mineru,
        verify_minimax=verify_minimax,
        verify_siliconflow=verify_siliconflow,
        parent=parent,
    ).show()


__all__ = [
    "CredentialDialog",
    "credentials_complete",
    "normalize_credential_values",
    "MODEL_ROLE_OPTIONS",
    "show_credential_dialog",
    "validate_credential_values",
    "validate_model_preferences",
    "verify_credential_accounts",
]

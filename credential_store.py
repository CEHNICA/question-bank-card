"""Keep API credentials encrypted and non-secret model preferences separate.

The encrypted file lives outside the question-bank folder so copies and ZIP
archives cannot accidentally include it. Model role choices contain no secrets
and live in a small adjacent JSON file. No third-party package is needed.
"""

from __future__ import annotations

import ctypes
import getpass
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping


class CredentialStoreError(RuntimeError):
    """A credential file could not be protected or read."""


# siliconflow_key is optional: in 题卡版 it makes the second reader a different vendor
# (Qwen-VL); in M3 it does the zoomed third reading. The file is shared by both.
CREDENTIAL_KEYS = ("mineru_token", "minimax_key", "siliconflow_key")
MAX_ACCOUNT_POOL_SIZE = 8
ACCOUNT_POOL_FIELDS = {
    "mineru": ("mineru_token", "mineru_tokens"),
    "minimax": ("minimax_key", "minimax_keys"),
    "siliconflow": ("siliconflow_key", "siliconflow_keys"),
}

MINIMAX_MODEL = "MiniMax-M3"
SILICONFLOW_MODEL = "Qwen/Qwen3-VL-32B-Instruct"
DEFAULT_MODEL_PREFERENCES = {
    "primary_engine": "minimax_m3",
    "checker_engine": "auto",
    "arbiter_engine": "primary",
}
MODEL_PREFERENCE_CHOICES = {
    "primary_engine": ("minimax_m3", "siliconflow_qwen3"),
    "checker_engine": ("auto", "minimax_m3", "siliconflow_qwen3"),
    "arbiter_engine": ("primary", "checker", "minimax_m3", "siliconflow_qwen3"),
}
MODEL_ENVIRONMENT_KEYS = frozenset({
    "QB_PRIMARY_ENGINE", "QB_CHECKER_ENGINE", "QB_ARBITER_ENGINE",
    "QB_MINIMAX_MODEL", "QB_SILICONFLOW_MODEL", "QB_MODEL_PREFERENCES_FILE",
})


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def credential_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise CredentialStoreError("找不到当前用户的 LocalAppData 目录。")
    return Path(local) / "QuestionBankM2" / "credentials.dat"


def model_preferences_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise CredentialStoreError("找不到当前用户的 LocalAppData 目录。")
    return Path(local) / "QuestionBankM2" / "model-preferences.json"


def normalize_model_preferences(values: dict[str, str] | None) -> dict[str, str]:
    """Return only supported role choices, falling back field-by-field."""
    source = values if isinstance(values, dict) else {}
    return {
        key: source.get(key) if source.get(key) in MODEL_PREFERENCE_CHOICES[key] else default
        for key, default in DEFAULT_MODEL_PREFERENCES.items()
    }


def load_model_preferences(path: Path | None = None) -> dict[str, str]:
    """Load non-secret role choices; a missing file means legacy defaults."""
    path = path or model_preferences_path()
    if not path.is_file():
        return dict(DEFAULT_MODEL_PREFERENCES)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise CredentialStoreError("已保存的模型偏好无法读取，将使用默认选择；可打开“配置 API”重新保存。") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("roles"), dict):
        raise CredentialStoreError("已保存的模型偏好格式不受支持，将使用默认选择；可打开“配置 API”重新保存。")
    return normalize_model_preferences(payload["roles"])


def save_model_preferences(values: dict[str, str], path: Path | None = None) -> None:
    """Atomically save the allow-listed, non-secret role choices."""
    path = path or model_preferences_path()
    payload = json.dumps(
        {"version": 1, "roles": normalize_model_preferences(values)},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n",
            prefix="model-preferences-", suffix=".tmp", dir=path.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(path)
    except OSError as exc:
        raise CredentialStoreError("无法保存当前用户的模型偏好。") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def model_preference_environment(values: dict[str, str] | None = None) -> dict[str, str]:
    """Return the non-secret environment contract for web and worker processes.

    QB_PRIMARY_ENGINE: minimax_m3 | siliconflow_qwen3
    QB_CHECKER_ENGINE: auto | minimax_m3 | siliconflow_qwen3
    QB_ARBITER_ENGINE: primary | checker | minimax_m3 | siliconflow_qwen3
    QB_MINIMAX_MODEL and QB_SILICONFLOW_MODEL pin the corresponding model IDs.
    QB_MODEL_PREFERENCES_FILE is the shared absolute path for atomic UI updates.
    """
    roles = normalize_model_preferences(values)
    return {
        "QB_PRIMARY_ENGINE": roles["primary_engine"],
        "QB_CHECKER_ENGINE": roles["checker_engine"],
        "QB_ARBITER_ENGINE": roles["arbiter_engine"],
        "QB_MINIMAX_MODEL": MINIMAX_MODEL,
        "QB_SILICONFLOW_MODEL": SILICONFLOW_MODEL,
        "QB_MODEL_PREFERENCES_FILE": str(model_preferences_path().resolve()),
    }


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _transform(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise CredentialStoreError("本机凭据保存仅支持 Windows。")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    input_blob, input_buffer = _blob(data)
    output_blob = _DataBlob()
    if protect:
        operation = crypt.CryptProtectData
        operation.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.c_wchar_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_DataBlob),
        ]
        arguments = (ctypes.byref(input_blob), "QuestionBankM2", None, None, None, 0x1, ctypes.byref(output_blob))
    else:
        operation = crypt.CryptUnprotectData
        operation.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_DataBlob),
        ]
        arguments = (ctypes.byref(input_blob), None, None, None, None, 0x1, ctypes.byref(output_blob))
    operation.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        if not operation(*arguments):
            raise CredentialStoreError("当前用户的凭据加密或读取失败。")
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if output_blob.pbData:
            kernel.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
        del input_buffer


def _normalize_account(service: str, value: object) -> str:
    if not isinstance(value, str):
        raise CredentialStoreError("已保存的凭据格式不受支持，请重新设置。")
    account = value.strip()
    if service == "mineru" and account.lower().startswith("bearer "):
        account = account[7:].strip()
    if service == "minimax":
        account = account.replace("\\_", "_")
    if not account:
        return ""
    if any(character.isspace() for character in account):
        raise CredentialStoreError("API 凭据中不能包含空格或换行，请重新设置。")
    if any(ord(character) < 32 or ord(character) == 127 for character in account):
        raise CredentialStoreError("API 凭据中包含不可见字符，请重新设置。")
    if len(account) > 16_384:
        raise CredentialStoreError("API 凭据过长，请重新设置。")
    return account


def credential_pool(values: Mapping[str, object] | None, service: str) -> list[str]:
    """Return one normalized, ordered account pool with legacy fallback.

    Version-1 credential files originally contained only the singular field.
    New files retain that field for old readers and add an encrypted list field.
    """

    if service not in ACCOUNT_POOL_FIELDS:
        raise ValueError(f"unsupported credential service: {service}")
    source = values if isinstance(values, Mapping) else {}
    legacy_key, pool_key = ACCOUNT_POOL_FIELDS[service]
    if pool_key in source:
        raw = source.get(pool_key)
        if not isinstance(raw, (list, tuple)):
            raise CredentialStoreError("已保存的账号池格式不受支持，请重新设置。")
        candidates = list(raw)
    else:
        legacy = source.get(legacy_key, "")
        candidates = [legacy] if legacy else []

    pool: list[str] = []
    for candidate in candidates:
        account = _normalize_account(service, candidate)
        if account and account not in pool:
            pool.append(account)
    if len(pool) > MAX_ACCOUNT_POOL_SIZE:
        raise CredentialStoreError(f"每类 API 最多保存 {MAX_ACCOUNT_POOL_SIZE} 个账号。")
    return pool


def load_credentials(path: Path | None = None) -> dict[str, object]:
    path = path or credential_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(_transform(path.read_bytes(), protect=False).decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise CredentialStoreError(
                "已保存的凭据格式不受支持，请打开“题库题卡版 - 配置 API”重新设置。"
            )
        result: dict[str, object] = {}
        for service, (legacy_key, pool_key) in ACCOUNT_POOL_FIELDS.items():
            pool = credential_pool(payload, service)
            if pool:
                result[legacy_key] = pool[0]
                result[pool_key] = pool
        return result
    except (OSError, UnicodeError, ValueError, TypeError, CredentialStoreError) as exc:
        raise CredentialStoreError("已保存的凭据无法读取，请打开“题库题卡版 - 配置 API”重新设置。") from exc


def save_credentials(values: Mapping[str, object], path: Path | None = None) -> None:
    path = path or credential_path()
    clean: dict[str, object] = {}
    for service, (legacy_key, pool_key) in ACCOUNT_POOL_FIELDS.items():
        pool = credential_pool(values, service)
        if pool:
            # Keep the original singular field so pre-pool releases and the
            # separately installed M3 tool can still use the first account.
            clean[legacy_key] = pool[0]
            clean[pool_key] = pool
    if not clean:
        clear_credentials(path)
        return
    payload = json.dumps({"version": 1, **clean}, ensure_ascii=False).encode("utf-8")
    protected = _transform(payload, protect=True)
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="wb", prefix="credentials-", suffix=".tmp", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(protected)
        temporary.replace(path)
    except OSError as exc:
        raise CredentialStoreError("无法保存当前用户的凭据。") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def clear_credentials(path: Path | None = None) -> None:
    try:
        (path or credential_path()).unlink(missing_ok=True)
    except OSError as exc:
        raise CredentialStoreError("无法清除当前用户保存的凭据。") from exc


def _read_saved() -> dict[str, object]:
    try:
        return load_credentials()
    except CredentialStoreError as exc:
        print(exc)
        return {}


def manage() -> int:
    if os.name != "nt":
        print("此工具仅支持 Windows。")
        return 1
    while True:
        print("\n管理当前 Windows 用户保存的题库凭据")
        print("1. 设置或更换 MinerU Token 账号池")
        print("2. 设置或更换 MiniMax API Key 账号池")
        print("3. 设置或更换 硅基流动 API Key 账号池（可选）")
        print("4. 清除全部已保存凭据")
        print("5. 退出")
        choice = input("请选择 1–5：").strip()
        if choice == "5" or not choice:
            return 0
        if choice == "3":
            value = getpass.getpass(
                "输入硅基流动 API Key（多个用英文分号分隔；留空取消；输入“删除”移除）："
            ).strip()
            if not value:
                print("已取消。")
                continue
            current = _read_saved()
            if value == "删除":
                current.pop("siliconflow_key", None)
                current["siliconflow_keys"] = []
            else:
                current.pop("siliconflow_key", None)
                current["siliconflow_keys"] = value.split(";")
            try:
                save_credentials(current)
            except CredentialStoreError as exc:
                print(exc)
                return 1
            print("已加密保存。重新启动题库后生效。" if value != "删除" else "已移除；重新启动后改由 MiniMax 再读一遍。")
            continue
        if choice in ("1", "2"):
            environment_name = "MINERU_TOKEN" if choice == "1" else "MINIMAX_API_KEY"
            environment_override = bool(os.environ.get(environment_name, "").strip())
            if environment_override and choice == "1":
                print(f"注意：{environment_name} 环境变量优先于这里保存的值。要使用新的保存值，请先更换或移除该环境变量。")
            elif environment_override:
                print("检测到 MINIMAX_API_KEY 环境变量；新保存的 MiniMax Key 将在重新启动后优先使用。")
            value = getpass.getpass("输入新的凭据（多个用英文分号分隔；输入时不显示，留空取消）：").strip()
            if not value:
                print("已取消。")
                continue
            if choice == "1" and value.lower().startswith("bearer "):
                value = value[7:].strip()
            if choice == "2":
                value = value.replace("\\_", "_")
            if not value:
                print("已取消。")
                continue
            current = _read_saved()
            legacy_key = "mineru_token" if choice == "1" else "minimax_key"
            pool_key = "mineru_tokens" if choice == "1" else "minimax_keys"
            current.pop(legacy_key, None)
            current[pool_key] = value.split(";")
            try:
                save_credentials(current)
            except CredentialStoreError as exc:
                print(exc)
                return 1
            if environment_override and choice == "1":
                print(f"已加密保存；当前仍由 {environment_name} 环境变量优先提供凭据。更换或移除该环境变量后，重新启动题库即可使用保存值。")
            else:
                print("已加密保存。重新启动题库后生效。")
        elif choice == "4":
            if input("确定清除当前用户保存的全部题库凭据？输入“清除”确认：").strip() == "清除":
                try:
                    clear_credentials()
                except CredentialStoreError as exc:
                    print(exc)
                    return 1
                print("已清除；下次启动时可重新输入。")
            else:
                print("已取消。")
        else:
            print("请输入 1–5。")


if __name__ == "__main__":
    raise SystemExit(manage())

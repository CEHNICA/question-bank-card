"""Keep optional API credentials encrypted for the current Windows user.

The encrypted file lives outside the question-bank folder so copies and ZIP
archives cannot accidentally include it.  No third-party package is needed.
"""

from __future__ import annotations

import ctypes
import getpass
import json
import os
import tempfile
from pathlib import Path


class CredentialStoreError(RuntimeError):
    """A credential file could not be protected or read."""


# siliconflow_key is optional: in 题卡版 it makes the second reader a different vendor
# (Qwen-VL); in M3 it does the zoomed third reading. The file is shared by both.
CREDENTIAL_KEYS = ("mineru_token", "minimax_key", "siliconflow_key")


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def credential_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise CredentialStoreError("找不到当前用户的 LocalAppData 目录。")
    return Path(local) / "QuestionBankM2" / "credentials.dat"


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


def load_credentials(path: Path | None = None) -> dict[str, str]:
    path = path or credential_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(_transform(path.read_bytes(), protect=False).decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError, CredentialStoreError) as exc:
        raise CredentialStoreError("已保存的凭据无法读取，请打开“题库题卡版 - 配置 API”重新设置。") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise CredentialStoreError("已保存的凭据格式不受支持，请打开“题库题卡版 - 配置 API”重新设置。")
    return {
        key: payload[key]
        for key in CREDENTIAL_KEYS
        if isinstance(payload.get(key), str)
    }


def save_credentials(values: dict[str, str], path: Path | None = None) -> None:
    path = path or credential_path()
    clean = {
        key: values[key]
        for key in CREDENTIAL_KEYS
        if isinstance(values.get(key), str)
    }
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


def _read_saved() -> dict[str, str]:
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
        print("1. 设置或更换 MinerU Token")
        print("2. 设置或更换 MiniMax API Key")
        print("3. 设置或更换 硅基流动 API Key（可选：让另一家模型做第二位读者 / M3 的第三评）")
        print("4. 清除全部已保存凭据")
        print("5. 退出")
        choice = input("请选择 1–5：").strip()
        if choice == "5" or not choice:
            return 0
        if choice == "3":
            value = getpass.getpass("输入硅基流动 API Key（输入时不显示，留空取消；输入“删除”移除已保存的值）：").strip()
            if not value:
                print("已取消。")
                continue
            current = _read_saved()
            if value == "删除":
                current.pop("siliconflow_key", None)
            else:
                current["siliconflow_key"] = value
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
            value = getpass.getpass("输入新的凭据（输入时不显示，留空取消）：").strip()
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
            current["mineru_token" if choice == "1" else "minimax_key"] = value
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

"""Non-secret model-role preferences shared with the Windows launcher."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from django.conf import settings


DEFAULTS = {
    "primary_engine": "minimax_m3",
    "checker_engine": "auto",
    "arbiter_engine": "primary",
}
CHOICES = {
    "primary_engine": {"minimax_m3", "siliconflow_qwen3"},
    "checker_engine": {"auto", "minimax_m3", "siliconflow_qwen3"},
    "arbiter_engine": {"primary", "checker", "minimax_m3", "siliconflow_qwen3"},
}


class PreferenceError(RuntimeError):
    pass


def preference_path() -> Path:
    explicit = os.environ.get("QB_MODEL_PREFERENCES_FILE", "").strip()
    if explicit:
        return Path(explicit).resolve()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return (Path(local) / "QuestionBankM2" / "model-preferences.json").resolve()
    # Development/Linux fallback. It contains no API keys.
    return (settings.DATA_ROOT / "model-preferences.json").resolve()


def normalize(values) -> dict[str, str] | None:
    if not isinstance(values, dict):
        return None
    result = {}
    for key, default in DEFAULTS.items():
        value = values.get(key, default)
        if value not in CHOICES[key]:
            return None
        result[key] = value
    return result


def load() -> dict[str, str]:
    path = preference_path()
    if not path.is_file():
        return dict(DEFAULTS)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise PreferenceError("模型设置文件无法读取，请重新保存") from exc
    roles = value.get("roles") if isinstance(value, dict) and value.get("version") == 1 else None
    normalized = normalize(roles)
    if normalized is None:
        raise PreferenceError("模型设置文件格式不正确，请重新保存")
    return normalized


def save(values: dict[str, str]) -> dict[str, str]:
    normalized = normalize(values)
    if normalized is None:
        raise PreferenceError("模型选择不受支持")
    path = preference_path()
    payload = json.dumps({"version": 1, "roles": normalized}, ensure_ascii=False, indent=2) + "\n"
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", prefix="model-preferences-",
            suffix=".tmp", dir=path.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(path)
    except OSError as exc:
        raise PreferenceError("模型设置暂时无法保存") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return normalized

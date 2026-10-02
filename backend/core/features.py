"""功能开关：设置页“功能开关”里的几项，存在数据目录的 features.json。

网页和后台工作者共用同一个数据目录，所以两边读到的是同一份开关。
文件里没有密钥。缺文件、文件坏了都按默认值处理，不影响读题。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from django.conf import settings

FEATURES = {
    # 默认开：只挪格式、不改字。
    "origin_split": {
        "default": True, "label": "拆出题源",
        "help": "题干开头的“[2026××中学月考]”“（2025·北京·期中）”放到“题源”里，组卷打印时不印。",
    },
    "chinese_quotes": {
        "default": True, "label": "中文引号",
        "help": "中文句子里的英文双引号 \"…\" 换成 “…”。公式里的不动。",
    },
    # 默认关：要用的时候再打开。
    "subquestions": {
        "default": False, "label": "显示小问数",
        "help": "题库里标出“含 2 小问”，以后按小问布置作业时用。",
    },
    "knowledge_tags": {
        "default": False, "label": "知识点标签",
        "help": "默认关闭。开启后由当前豆包或 AI 助手从固定目录选 1–3 个标签并写回；也可在独立设置中选择模型 API。AI 标签需核对。",
    },
    "ai_answer": {
        "default": False, "label": "AI 参考答案",
        "help": "默认关闭。开启后由当前豆包或 AI 助手为没有原卷答案的题生成参考并写回，无需额外豆包 API。可选独立模型；结果标着“AI 参考 · 未核对”，与原卷答案分开。",
    },
}
DEFAULTS = {key: spec["default"] for key, spec in FEATURES.items()}

_lock = threading.Lock()
_cache: dict = {"path": None, "mtime": None, "values": None}


class FeatureError(ValueError):
    pass


def path() -> Path:
    explicit = os.environ.get("QB_FEATURES_FILE", "").strip()
    if explicit:
        return Path(explicit).resolve()
    return (Path(settings.DATA_ROOT) / "features.json").resolve()


def _read(target: Path) -> dict:
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load() -> dict[str, bool]:
    """Current switches; unknown keys and non-boolean values fall back to the defaults."""
    target = path()
    try:
        mtime = target.stat().st_mtime_ns
    except OSError:
        mtime = None
    with _lock:
        if _cache["path"] == target and _cache["mtime"] == mtime and _cache["values"] is not None:
            return dict(_cache["values"])
    stored = _read(target) if mtime is not None else {}
    values = {key: stored[key] if isinstance(stored.get(key), bool) else default
              for key, default in DEFAULTS.items()}
    with _lock:
        _cache.update(path=target, mtime=mtime, values=dict(values))
    return values


def enabled(name: str) -> bool:
    return bool(load().get(name, DEFAULTS.get(name, False)))


def save(changes) -> dict[str, bool]:
    """Change some switches (others keep their value) and return all of them."""
    if not isinstance(changes, dict) or not changes:
        raise FeatureError("请提供要修改的开关")
    unknown = set(changes) - set(DEFAULTS)
    if unknown:
        raise FeatureError("没有这个开关：" + "、".join(sorted(unknown)))
    if not all(isinstance(value, bool) for value in changes.values()):
        raise FeatureError("开关只能是 true 或 false")
    values = {**load(), **changes}
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".features-", suffix=".json", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    with _lock:
        _cache.update(path=None, mtime=None, values=None)
    return load()


def describe() -> list[dict]:
    """The switches with their labels, for the settings page and tiyouju."""
    values = load()
    return [{"key": key, "label": spec["label"], "help": spec["help"], "default": spec["default"],
             "enabled": values[key]} for key, spec in FEATURES.items()]

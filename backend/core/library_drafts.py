"""Named paper drafts: ordered publication IDs and print choices, never question text or keys."""

from __future__ import annotations

import errno
import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .library_browse import BrowseError, normalize_ids, resolve_ids

MAX_DRAFTS = 100
MAX_STORE_BYTES = 2 * 1024 * 1024
MAX_TITLE_LENGTH = 120
LEGACY_PRINT_KEYS = {"answers", "origin", "ai_answers"}
# 1.12.9：默认只出题目卷。answers 这一栏下面第 75 行会按 document 重算，
# 这里跟着改只是别让常量自相矛盾。
PRINT_DEFAULTS = {
    "answers": False,
    "origin": False,
    "ai_answers": False,
    "document": "questions",
    "font_size": 12,
    "answer_space": "auto",
    "student_info": True,
    "pagination": "compact",
    "option_layout": "auto",
    "option_overrides": {},
    # 预览量出来的每道题最终排成几栏。Word 照它排，别再自己猜一遍 ——
    # 两边各猜各的，屏幕上四栏、文件里竖排，老师核对的是屏幕、拿到手的是文件。
    "option_columns": {},
    # 预览量出来的每张图在纸面上实际有多宽（毫米）：题目编号 → 位置 → 宽度列表。
    # 选项里的图在屏幕上会被选项栏宽收口，Word 不知道这件事，只能照这个宽度排。
    "measured_figure_widths": {},
    # 预览量出来的每张表离版心左缘多远（毫米）：题目编号 → 位置 → 偏移列表。
    # 表格在预览里的横向位置不是定值（题干正文越长，表在那一列里越靠中间），
    # Word 排版是静态的，只能照这个位置摆。
    "measured_table_offsets": {},
    # 预览量出来的答题留白在纸上被切成了几段、每段是不是落在续页上：
    # 题目编号 → [{"mm": 该段高度, "continuation": 这段是不是续页上的}, …]。
    # 预览的留白是**可切**的（frontend/exam-layout.js 的 writingSpace）：页底放不下的
    # 那几毫米挪到下一页，续页顶部还打一行「第 N 题答题区（续）」。
    # Word 排版是静态的，自己不知道该在哪儿切、也不知道该打这行标题，
    # 只能照这里量好的来 —— 老师核对的是屏幕上那份。
    "measured_answer_splits": {},
    "question_breaks": [],
    "answer_layout": "inline",
    "answer_space_overrides": {},
    # 全卷配图整行默认位置；单题 figure_overrides.align 可覆盖。靠右更贴卷面习惯。
    "figure_align": "right",
    "figure_overrides": {},
    # 大题组织方式："by_type" 按题型自动归组；"basket_order" 按当前选题顺序混排（不拆大题）。
    "group_mode": "by_type",
}
# 留白（answer_space）有两种形态：
#
#   "auto"  按题型自动 —— 选择题不留白，解答题留 30 毫米。
#   数字     毫米数，0 到 200。0 表示不留白（解答题另有 8 毫米下限）。
#
# 旧草稿存的是 none/small/medium/large，这里继续认，落到毫米数上。
LEGACY_SPACES = {"none": 0, "small": 12, "medium": 30, "large": 60}
SPACE_MAX_MM = 200
# 一道题的留白最多被切成几段（页底一段 + 每张续页一段）。留白最大 200 毫米，
# 版心一页 261 毫米，所以实际最多切两段；留个余量给以后更小的版心。
ANSWER_SPLIT_MAX_PIECES = 10
# 解答题保底：再挤也得给学生留两行（和前端 exam-layout.js 的 FREE_RESPONSE_MIN 一致）。
FREE_RESPONSE_MIN_MM = 8
# 「智能」档下解答题留多少（对应前端 AUTO_FREE_RESPONSE）。
AUTO_FREE_RESPONSE_MM = 30


def _answer_space_millimetres(value, question_type):
    """一道题最终留白多少毫米。0 表示不留白。

    口径必须和前端 exam-layout.js 的 answerSpace() 一致：预览看到的留白
    必须等于导出的留白（PDF 走 library_pdf.py 里的同名逻辑）。
    """
    if value == "auto":
        millimetres = AUTO_FREE_RESPONSE_MM if question_type == "free_response" else 0
    else:
        millimetres = _space_millimetres(value)
        if millimetres is None or millimetres == "auto":
            millimetres = 0
    if question_type == "free_response" and millimetres < FREE_RESPONSE_MIN_MM:
        millimetres = FREE_RESPONSE_MIN_MM
    return millimetres


def _space_millimetres(value):
    """归一化一个留白设置。返回 "auto" 或毫米数；不合法返回 None。"""
    if isinstance(value, str) and value == "auto":
        return "auto"
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        millimetres = float(value)
        if millimetres != millimetres or millimetres in (float("inf"), float("-inf")):
            return None
        millimetres = max(0.0, min(float(SPACE_MAX_MM), millimetres))
        return int(millimetres) if millimetres.is_integer() else round(millimetres, 2)
    if isinstance(value, str) and value in LEGACY_SPACES:
        return LEGACY_SPACES[value]
    return None


# 配图调整（figure_overrides）：老师在自己这份卷子里把题干配图放大缩小、决定每行靠左还是
# 靠右、点名让某张图从下一行开始。跟着草稿存，不写回题库 —— 换一份卷子、换一个老师，
# 题库里那道题还是原样。
#
#   {"<题目编号>": {"width": {"0": 60.5}, "align": "left", "breaks": [2]}}
#
# width 的键是这道题题干配图里的第几张（从 0 数，和预览里从上往下看到的顺序一致），
# 值是这张图在纸上的宽度（毫米）。align 管这一题所有配图行，breaks 里的序号从该行开始。
FIGURE_ALIGNMENTS = ("left", "center", "right")
FIGURE_WIDTH_MIN_MM = 5
FIGURE_WIDTH_MAX_MM = 178
FIGURE_MAX_PER_QUESTION = 50
# 预览量出来的图宽能记哪些位置：题干那一行，以及 A 到 E 各选项。
# 原图题（正文整段是截图）走的是另一条路，不在这里。
MEASURED_FIGURE_SLOTS = frozenset({"stem", "A", "B", "C", "D", "E"})
# 预览量出来的表格位置能记哪儿：题干、答案正文、解析。
MEASURED_TABLE_SLOTS = frozenset({"stem", "answer", "analysis"})
TABLE_MAX_PER_QUESTION = 50
INPUT_FIELDS = {"title", "ids", "print_options", "revision", "solutions"}
STORED_FIELDS = {"id", "title", "ids", "print_options", "created_at", "updated_at", "revision"}
_LOCK = threading.RLock()


class DraftError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def draft_path() -> Path:
    return Path(settings.DATA_ROOT) / "library-drafts.json"


def _title(value) -> str:
    if not isinstance(value, str):
        raise DraftError("草稿名称应为文字")
    value = value.strip()
    if not value or len(value) > MAX_TITLE_LENGTH or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise DraftError(f"请填写 1–{MAX_TITLE_LENGTH} 字的草稿名称，不含换行或控制字符")
    return value


def _figure_index(value):
    """Figure positions arrive as 0-based ints or the strings JSON object keys become."""
    if isinstance(value, bool):
        raise DraftError("配图序号应为数字")
    if isinstance(value, str):
        if not value.isdigit():
            raise DraftError("配图序号应为数字")
        value = int(value)
    if not isinstance(value, int) or not 0 <= value < FIGURE_MAX_PER_QUESTION:
        raise DraftError(f"配图序号应在第 1–{FIGURE_MAX_PER_QUESTION} 张之间")
    return value


def _figure_overrides(value):
    """Check one draft's figure adjustments, keeping only what changes something.

    The keys are question numbers, normalised by ``normalize_ids`` like every other
    per-question override, so a draft saved as ``1EF667E5…`` and one saved as
    ``1ef667e5…`` are the same entry. ``width`` values are millimetres on paper —
    the unit the number box in the preview asks for and the unit Word writes, so
    "60" in the preview and "60" on the page are the same width.

    整行位置缺省跟随全卷 ``figure_align``（默认靠右）。单题显式选了靠左 / 居中 /
    靠右才写入 ``align``；没动过位置的题不记这一项。
    """
    if not isinstance(value, dict) or len(value) > 500:
        raise DraftError("配图调整最多 500 题")
    checked = {}
    for question, setting in value.items():
        if not isinstance(setting, dict) or set(setting) - {"width", "align", "breaks"}:
            raise DraftError("配图调整只能包含图的大小、整行位置和从哪张图换行")
        align = setting.get("align")
        if align is not None and align not in FIGURE_ALIGNMENTS:
            raise DraftError("配图整行位置只能选靠左、居中或靠右")
        widths = setting.get("width", {})
        if not isinstance(widths, dict) or len(widths) > FIGURE_MAX_PER_QUESTION:
            raise DraftError(f"每题最多调整 {FIGURE_MAX_PER_QUESTION} 张配图")
        clean_widths = {}
        for key, millimetres in widths.items():
            index = _figure_index(key)
            if (
                isinstance(millimetres, bool)
                or not isinstance(millimetres, (int, float))
                or not FIGURE_WIDTH_MIN_MM <= millimetres <= FIGURE_WIDTH_MAX_MM
            ):
                raise DraftError(f"配图宽度应为 {FIGURE_WIDTH_MIN_MM}–{FIGURE_WIDTH_MAX_MM} 毫米之间的数字")
            clean_widths[index] = round(float(millimetres), 1)
        breaks = setting.get("breaks", [])
        if not isinstance(breaks, list) or len(breaks) > FIGURE_MAX_PER_QUESTION:
            raise DraftError(f"每题最多指定 {FIGURE_MAX_PER_QUESTION} 处换行")
        clean_breaks = sorted({_figure_index(index) for index in breaks})
        entry = {}
        if clean_widths:
            entry["width"] = dict(sorted(clean_widths.items()))
        if align in FIGURE_ALIGNMENTS:
            entry["align"] = align
        if clean_breaks:
            entry["breaks"] = clean_breaks
        if entry:
            checked[question] = entry
    return checked


def _print_options(value, defaults=None, *, ids=None) -> dict:
    if not isinstance(value, dict) or set(value) - set(PRINT_DEFAULTS):
        raise DraftError("打印设置包含不支持的选项")
    if any(type(value[key]) is not bool for key in (LEGACY_PRINT_KEYS | {"student_info"}) & set(value)):
        raise DraftError("打印开关只能是 true 或 false")
    if "font_size" in value and (type(value["font_size"]) is not int or value["font_size"] not in (12, 14, 16)):
        raise DraftError("字号只能选 12、14、16 磅")
    if "document" in value and value["document"] not in ("questions", "answers", "combined"):
        raise DraftError("请选择题目卷、答案解析卷或题目与答案合卷")
    if "answer_space" in value and _space_millimetres(value["answer_space"]) is None:
        raise DraftError("答题留白只能是 auto 或 0 到 200 之间的毫米数")
    if "pagination" in value and value["pagination"] not in ("compact", "keep"):
        raise DraftError("分页只能选紧凑排版或尽量整题同页")
    if "option_layout" in value and value["option_layout"] not in ("auto", "four", "two", "vertical"):
        raise DraftError("选项排版只能选 auto、four、two、vertical")
    if "answer_layout" in value and value["answer_layout"] not in ("inline", "appendix"):
        raise DraftError("答案位置只能选 inline 或 appendix")
    if "figure_align" in value and value["figure_align"] not in FIGURE_ALIGNMENTS:
        raise DraftError("配图整行位置只能选靠左、居中或靠右")
    if "group_mode" in value and value["group_mode"] not in ("by_type", "basket_order"):
        raise DraftError("大题组织方式只能选按题型归组或按选题顺序混排")
    result = {**deepcopy(PRINT_DEFAULTS), **deepcopy(defaults or {}), **deepcopy(value)}
    if result.get("figure_align") not in FIGURE_ALIGNMENTS:
        result["figure_align"] = "right"
    if result.get("group_mode") not in ("by_type", "basket_order"):
        result["group_mode"] = "by_type"
    if "document" not in value and "answers" in value:
        result["document"] = "combined" if value["answers"] else "questions"
    result["answers"] = result["document"] != "questions"
    # 全局留白归一化：auto 或毫米数。默认值和草稿里存的值都从这里过一遍。
    answer_space = _space_millimetres(result["answer_space"])
    if answer_space is None:
        raise DraftError("答题留白只能是 auto 或 0 到 200 之间的毫米数")
    result["answer_space"] = answer_space
    overrides = result["option_overrides"]
    if (
        not isinstance(overrides, dict)
        or len(overrides) > 500
        or any(mode not in ("auto", "four", "two", "vertical") for mode in overrides.values())
    ):
        raise DraftError("单题选项排版应为题目编号到 auto、four、two、vertical 的映射，最多 500 题")
    spaces = result["answer_space_overrides"]
    if not isinstance(spaces, dict) or len(spaces) > 500:
        raise DraftError("单题留白应为题目编号到留白毫米的映射，最多500题")
    normalized_spaces = []
    for mode in spaces.values():
        millimetres = _space_millimetres(mode)
        if millimetres is None:
            raise DraftError("单题留白只能是 auto 或 0 到 200 之间的毫米数")
        normalized_spaces.append(millimetres)
    try:
        figures = _figure_overrides(result["figure_overrides"])
        keys = normalize_ids(list(overrides))
        space_keys = normalize_ids(list(spaces))
        figure_keys = normalize_ids(list(figures))
        breaks = normalize_ids(result["question_breaks"])
        selected = set(normalize_ids(ids)) if ids is not None else None
    except BrowseError as error:
        raise DraftError("单题排版或另起页的题目编号不正确：" + str(error)) from None
    if (
        len(keys) != len(overrides)
        or len(space_keys) != len(spaces)
        or len(breaks) != len(result["question_breaks"])
        or len(figure_keys) != len(figures)
    ):
        raise DraftError("单题排版或另起页的题目编号不能重复")
    # 预览量出来的栏数：题目编号 → 1 到 5 的整数。写错的值不能进导出。
    columns = result["option_columns"]
    if not isinstance(columns, dict) or len(columns) > 500:
        raise DraftError("单题选项栏数应为题目编号到栏数的映射，最多 500 题")
    for value in columns.values():
        if type(value) is not int or not 1 <= value <= 5:
            raise DraftError("单题选项栏数只能是 1 到 5 之间的整数")
    try:
        column_keys = normalize_ids(list(columns))
    except BrowseError as error:
        raise DraftError("单题选项栏数的题目编号不正确：" + str(error)) from None
    if len(column_keys) != len(columns):
        raise DraftError("单题选项栏数的题目编号不能重复")
    # 预览量出来的每张图在纸面上实际有多宽：题目编号 → 位置 → 毫米列表。
    # 写错的宽度不能进导出 —— 老师核对的是屏幕，Word 照着一个错的宽度排，纸上就变形了。
    measured = result["measured_figure_widths"]
    if not isinstance(measured, dict) or len(measured) > 500:
        raise DraftError("单题配图实测宽度应为题目编号到宽度的映射，最多 500 题")
    measured_clean = {}
    for question, slots in measured.items():
        if not isinstance(slots, dict) or set(slots) - MEASURED_FIGURE_SLOTS:
            raise DraftError("单题配图实测宽度的位置只能是题干或 A 到 E")
        for values in slots.values():
            if not isinstance(values, list) or len(values) > FIGURE_MAX_PER_QUESTION:
                raise DraftError("每题最多测量 50 张配图")
            for value in values:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 178:
                    raise DraftError("配图实测宽度只能是 0 到 178 之间的毫米数")
        measured_clean[question] = {slot: list(values) for slot, values in slots.items()}
    try:
        measured_keys = normalize_ids(list(measured))
    except BrowseError as error:
        raise DraftError("单题配图实测宽度的题目编号不正确：" + str(error)) from None
    if len(measured_keys) != len(measured):
        raise DraftError("单题配图实测宽度的题目编号不能重复")
    # 预览量出来的每张表离版心左缘多远（毫米）：题目编号 → 位置 → 偏移列表。
    # 位置不对，表格就摆在错的地方 —— 写错的值不能进导出。
    tables = result["measured_table_offsets"]
    if not isinstance(tables, dict) or len(tables) > 500:
        raise DraftError("单题表格实测位置应为题目编号到位置的映射，最多 500 题")
    tables_clean = {}
    for question, slots in tables.items():
        if not isinstance(slots, dict) or set(slots) - MEASURED_TABLE_SLOTS:
            raise DraftError("单题表格实测位置只能是题干、答案或解析")
        for values in slots.values():
            if not isinstance(values, list) or len(values) > TABLE_MAX_PER_QUESTION:
                raise DraftError(f"每题最多测量 {TABLE_MAX_PER_QUESTION} 张表")
            for value in values:
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not 0 <= value <= FIGURE_WIDTH_MAX_MM
                ):
                    raise DraftError("表格实测位置只能是 0 到 178 之间的毫米数")
        tables_clean[question] = {slot: list(values) for slot, values in slots.items()}
    try:
        table_keys = normalize_ids(list(tables))
    except BrowseError as error:
        raise DraftError("单题表格实测位置的题目编号不正确：" + str(error)) from None
    if len(table_keys) != len(tables):
        raise DraftError("单题表格实测位置的题目编号不能重复")
    # 预览量出来的答题留白分段：题目编号 → [{"mm": 高度, "continuation": 是否续页}, …]。
    # 分段总高必须对得上这次要的留白，否则是预览改过、或者量的是另一份卷子 ——
    # 写错的值不能进导出，Word 照着一个错的分段排，留白高度就不是老师看过的那份。
    splits = result["measured_answer_splits"]
    if not isinstance(splits, dict) or len(splits) > 500:
        raise DraftError("单题留白实测分段应为题目编号到分段的映射，最多 500 题")
    splits_clean = {}
    for question, pieces in splits.items():
        if not isinstance(pieces, list) or not pieces or len(pieces) > ANSWER_SPLIT_MAX_PIECES:
            raise DraftError(f"每题留白实测分段最多 {ANSWER_SPLIT_MAX_PIECES} 段")
        cleaned = []
        for piece in pieces:
            if not isinstance(piece, dict) or set(piece) - {"mm", "continuation"}:
                raise DraftError("留白实测分段只能包含高度和是否续页")
            value = piece.get("mm")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= SPACE_MAX_MM:
                raise DraftError("留白实测分段高度只能是 0 到 200 之间的毫米数")
            flag = piece.get("continuation", False)
            if type(flag) is not bool:
                raise DraftError("留白实测分段的续页标记只能是 true 或 false")
            cleaned.append({"mm": float(value), "continuation": flag})
        splits_clean[question] = cleaned
    try:
        split_keys = normalize_ids(list(splits))
    except BrowseError as error:
        raise DraftError("单题留白实测分段的题目编号不正确：" + str(error)) from None
    if len(split_keys) != len(splits):
        raise DraftError("单题留白实测分段的题目编号不能重复")
    if (
        selected is not None
        and (
            set(keys)
            | set(space_keys)
            | set(figure_keys)
            | set(breaks)
            | set(column_keys)
            | set(measured_keys)
            | set(table_keys)
            | set(split_keys)
        )
        - selected
    ):
        raise DraftError("单题排版和另起页只能指定当前选中的题目")
    result["option_overrides"] = dict(zip(keys, overrides.values()))
    result["answer_space_overrides"] = dict(zip(space_keys, normalized_spaces))
    result["figure_overrides"] = dict(zip(figure_keys, figures.values()))
    result["question_breaks"] = breaks
    result["option_columns"] = dict(zip(column_keys, columns.values()))
    result["measured_figure_widths"] = dict(zip(measured_keys, measured_clean.values()))
    result["measured_table_offsets"] = dict(zip(table_keys, tables_clean.values()))
    result["measured_answer_splits"] = dict(zip(split_keys, splits_clean.values()))
    return result


def _revision(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1_000_000_000:
        raise DraftError("草稿版本编号不正确")
    return value


def _read() -> dict:
    target = draft_path()
    if target.is_symlink():
        raise DraftError("草稿文件不能是符号链接", 409)
    if not target.exists():
        return {"schema": 1, "drafts": []}
    try:
        if target.stat().st_size > MAX_STORE_BYTES:
            raise DraftError("草稿文件超过大小上限，未覆盖原文件", 409)
        raw = target.read_bytes()
        if len(raw) > MAX_STORE_BYTES:
            raise DraftError("草稿文件超过大小上限，未覆盖原文件", 409)
        data = json.loads(raw)
        if (
            not isinstance(data, dict)
            or set(data) != {"schema", "drafts"}
            or data["schema"] != 1
            or isinstance(data["schema"], bool)
            or not isinstance(data["drafts"], list)
            or len(data["drafts"]) > MAX_DRAFTS
        ):
            raise ValueError
        seen = set()
        for draft in data["drafts"]:
            if (
                not isinstance(draft, dict)
                or set(draft) - STORED_FIELDS - {"solutions"}
                or not set(draft) >= STORED_FIELDS
            ):
                raise ValueError
            key = str(uuid.UUID(draft["id"]))
            if key != draft["id"] or key in seen:
                raise ValueError
            seen.add(key)
            if _title(draft["title"]) != draft["title"] or normalize_ids(draft["ids"]) != draft["ids"]:
                raise ValueError
            if not isinstance(draft["print_options"], dict) or not set(draft["print_options"]) >= LEGACY_PRINT_KEYS:
                raise ValueError
            # Old saved papers intentionally retain their appendix layout.
            draft["print_options"] = _print_options(
                draft["print_options"], {"answer_layout": "appendix"}, ids=draft["ids"]
            )
            from .library_solutions import SolutionError, normalize_map

            try:
                fixed = normalize_map(draft.get("solutions", {}), draft["ids"])
                draft["solutions"] = {key: fixed.get(key, "origin") for key in draft["ids"]}
            except SolutionError as error:
                raise DraftError(str(error)) from None
            _revision(draft["revision"])
            for field in ("created_at", "updated_at"):
                if not isinstance(draft[field], str) or len(draft[field]) > 40:
                    raise ValueError
                from datetime import datetime

                datetime.fromisoformat(draft[field])
    except DraftError as error:
        raise DraftError("草稿文件内容异常，未覆盖原文件", 409) from error
    except (OSError, ValueError, TypeError, KeyError, AttributeError, BrowseError):
        raise DraftError("组卷草稿文件无法读取，未覆盖原文件", 409) from None
    return data


@contextmanager
def _write_lock():
    """Serialize both threads and desktop processes before read-modify-replace."""
    with _LOCK:
        target = draft_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_suffix(".lock")
        if lock_path.is_symlink():
            raise DraftError("草稿锁文件不能是符号链接", 409)
        with lock_path.open("a+b") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 5
            while True:
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    if time.monotonic() >= deadline:
                        raise DraftError("草稿正在被另一个窗口保存，请稍后重试", 409) from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write(data: dict) -> None:
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_STORE_BYTES:
        raise DraftError("组卷草稿总大小已达上限，请删去不再需要的草稿", 409)
    target = draft_path()
    if target.is_symlink():
        raise DraftError("草稿文件不能是符号链接", 409)
    descriptor, temporary = tempfile.mkstemp(prefix=".library-drafts-", suffix=".json", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _public(draft: dict) -> dict:
    checked = resolve_ids(draft["ids"], include_items=False)
    return {
        **draft,
        "validity": {
            "valid": not checked["missing"],
            "valid_count": checked["available_count"],
            "missing": checked["missing"],
        },
    }


def _payload(request) -> dict:
    from .views import _body

    payload = _body(request)
    if payload is None or set(payload) - INPUT_FIELDS:
        raise DraftError("只可保存草稿名称、题目编号、打印设置和草稿版本")
    return payload


def _save(payload: dict, draft_id=None) -> dict:
    with _write_lock():
        data = _read()
        draft = None
        if draft_id is not None:
            draft = next((item for item in data["drafts"] if item["id"] == str(draft_id)), None)
            if draft is None:
                raise DraftError("找不到这份组卷草稿", 404)
            if "revision" in payload and _revision(payload["revision"]) != draft["revision"]:
                raise DraftError("这份草稿已在其他窗口修改，请重新载入后再保存", 409)
        elif len(data["drafts"]) >= MAX_DRAFTS:
            raise DraftError(f"最多保存 {MAX_DRAFTS} 份组卷草稿，请先删去不再需要的草稿", 409)
        elif "revision" in payload:
            raise DraftError("新增草稿不需要提供版本编号")
        if draft is None and "ids" not in payload:
            raise DraftError("请提供按顺序排列的题目编号 ids")
        title = _title(payload.get("title", draft["title"] if draft else "练习"))
        try:
            ids = normalize_ids(payload.get("ids", draft["ids"] if draft else []))
        except BrowseError as error:
            raise DraftError(str(error)) from None
        defaults = deepcopy(draft["print_options"] if draft else PRINT_DEFAULTS)
        # Removing a question also removes its saved layout hint. Explicit new
        # hints for an unselected ID still fail validation rather than disappearing.
        defaults["option_overrides"] = {key: mode for key, mode in defaults["option_overrides"].items() if key in ids}
        defaults["answer_space_overrides"] = {
            key: mode for key, mode in defaults["answer_space_overrides"].items() if key in ids
        }
        defaults["figure_overrides"] = {key: mode for key, mode in defaults["figure_overrides"].items() if key in ids}
        defaults["question_breaks"] = [key for key in defaults["question_breaks"] if key in ids]
        options = _print_options(payload.get("print_options", {}), defaults, ids=ids)
        from .library_solutions import SolutionError, normalize_map

        try:
            previous_solutions = {
                key: value for key, value in (draft.get("solutions", {}) if draft else {}).items() if key in ids
            }
            solutions = normalize_map(payload.get("solutions", previous_solutions), ids)
            # Newly added entries capture their current library overlay once;
            # an original selection is explicit so later edits cannot alter it.
            from .models import PublishedQuestion

            current = {
                str(item.pk): (item.extras or {}).get("solution_id")
                for item in PublishedQuestion.objects.filter(pk__in=ids)
            }
            for key in ids:
                solutions.setdefault(key, current.get(key) or "origin")
        except SolutionError as error:
            raise DraftError(str(error), error.status) from None
        now = timezone.now().isoformat()
        result = {
            "id": str(draft_id) if draft else str(uuid.uuid4()),
            "title": title,
            "ids": ids,
            "print_options": options,
            "solutions": solutions,
            "created_at": draft["created_at"] if draft else now,
            "updated_at": now,
            "revision": draft["revision"] + 1 if draft else 1,
        }
        if draft is None:
            data["drafts"].append(result)
        else:
            data["drafts"][data["drafts"].index(draft)] = result
        _write(data)
    return result


@csrf_exempt
def drafts_view(request):
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    if request.method == "POST":
        from .views import _guard

        rejected = _guard(request)
        if rejected is not None:
            return rejected
    try:
        if request.method == "GET":
            data = _read()
            drafts = sorted(data["drafts"], key=lambda item: (item["updated_at"], item["id"]), reverse=True)
            return JsonResponse({"drafts": [_public(item) for item in drafts], "total": len(drafts)})
        return JsonResponse({"draft": _public(_save(_payload(request)))}, status=201)
    except DraftError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except OSError:
        return JsonResponse({"error": "组卷草稿没能保存，请检查数据目录是否可写"}, status=500)


@csrf_exempt
def draft_detail(request, draft_id):
    if request.method not in {"GET", "PUT", "DELETE"}:
        return HttpResponseNotAllowed(["GET", "PUT", "DELETE"])
    if request.method != "GET":
        from .views import _guard

        rejected = _guard(request)
        if rejected is not None:
            return rejected
    try:
        try:
            draft_id = str(uuid.UUID(str(draft_id)))
        except ValueError:
            raise DraftError("草稿编号格式不正确") from None
        if request.method == "PUT":
            return JsonResponse({"draft": _public(_save(_payload(request), draft_id))})
        if request.method == "GET":
            draft = next((item for item in _read()["drafts"] if item["id"] == draft_id), None)
            if draft is None:
                raise DraftError("找不到这份组卷草稿", 404)
            return JsonResponse({"draft": _public(draft)})
        with _write_lock():
            data = _read()
            before = len(data["drafts"])
            data["drafts"] = [item for item in data["drafts"] if item["id"] != draft_id]
            if len(data["drafts"]) == before:
                raise DraftError("找不到这份组卷草稿", 404)
            _write(data)
        return JsonResponse({"deleted": draft_id})
    except DraftError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except OSError:
        return JsonResponse({"error": "组卷草稿没能保存，请检查数据目录是否可写"}, status=500)

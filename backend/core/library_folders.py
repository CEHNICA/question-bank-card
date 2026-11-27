"""Persistent Eagle workspace folders, smart folders, sort modes, and undoable organization.

Stores ``user_folders``, ``doc_overrides``, ``smart_folders``, ``sort_modes``, and
``paper_sort_mode`` in ``DATA_ROOT / "library-folders.json"`` so both the Eagle web UI
and external AI agents (via CLI / MCP) share a single source of truth with undo support.
"""

from __future__ import annotations

import json
import re
import secrets
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import library
from .models import LibraryTag, Paper, PublishedQuestion

MAX_FOLDER_DEPTH = 8
MAX_FOLDERS = 5000
MAX_SMART_FOLDERS = 200
MAX_HISTORY = 50
MAX_NAME_LEN = 120
MAX_BATCH_QUESTIONS = 500
MAX_TREE_QUESTIONS = 5000
VALID_SORT_MODES = ("time_desc", "time_asc", "name", "count_desc", "manual")
DEFAULT_SORT_MODE = "time_desc"

_LOCK = threading.RLock()


class FolderError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def store_path() -> Path:
    return (Path(settings.DATA_ROOT) / "library-folders.json").resolve()


def _empty_state() -> dict:
    return {
        "version": 1,
        "revision": 0,
        "updated_at": "",
        "user_folders": [],
        "doc_overrides": {},
        "doc_placements": {},
        "custom_tree_docs": [],
        "smart_folders": [],
        "sort_modes": {},
        "paper_sort_mode": DEFAULT_SORT_MODE,
        "history": [],
    }


def _clean_name(value, label: str = "文件夹名称") -> str:
    if not isinstance(value, str):
        raise FolderError(f"{label}必须是文字")
    text = " ".join(value.split()).strip()
    if not text or len(text) > MAX_NAME_LEN or any(ord(ch) < 32 for ch in text):
        raise FolderError(f"{label}需为 1–{MAX_NAME_LEN} 个字符")
    return text


def _clean_sort_modes(raw) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, val in raw.items():
        k = str(key or "").strip()
        if k and val in VALID_SORT_MODES:
            out[k] = str(val)
    return out


def _clean_sort_order(value) -> int | None:
    """Preserve optional sibling order for Eagle-style manual folder arrange."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _clean_doc_overrides(raw) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for key, entry in raw.items():
        doc_id = str(key or "").strip()
        if not doc_id or not isinstance(entry, dict):
            continue
        added = list(dict.fromkeys(str(x) for x in (entry.get("added") or []) if str(x).strip()))
        removed = list(dict.fromkeys(str(x) for x in (entry.get("removed") or []) if str(x).strip()))
        raw_sections = entry.get("sections")
        sections: dict[str, dict] = {}
        if isinstance(raw_sections, dict):
            for qid_raw, meta in raw_sections.items():
                qid_str = str(qid_raw or "").strip()
                if not qid_str or not isinstance(meta, dict):
                    continue
                sec_item: dict[str, object] = {}
                if meta.get("chapterKey") is not None:
                    sec_item["chapterKey"] = str(meta["chapterKey"])[:80]
                if meta.get("chapterNo") is not None and isinstance(meta.get("chapterNo"), (int, float)):
                    sec_item["chapterNo"] = int(meta["chapterNo"])
                if meta.get("chapterGroupName"):
                    sec_item["chapterGroupName"] = str(meta["chapterGroupName"])[:MAX_NAME_LEN]
                if meta.get("sectionName") is not None:
                    sec_item["sectionName"] = str(meta["sectionName"])[:MAX_NAME_LEN]
                if meta.get("sectionMatchMode"):
                    sec_item["sectionMatchMode"] = str(meta["sectionMatchMode"])[:40]
                if meta.get("label"):
                    sec_item["label"] = str(meta["label"])[:MAX_NAME_LEN]
                if sec_item:
                    sections[qid_str] = sec_item
        if added or removed or sections:
            cleaned_entry: dict = {"added": added, "removed": removed}
            if sections:
                cleaned_entry["sections"] = sections
            out[doc_id] = cleaned_entry
    return out


def _clean_user_folders(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen_ids: set[str] = set()
    for item in raw[:MAX_FOLDERS]:
        if not isinstance(item, dict):
            continue
        fid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()[:MAX_NAME_LEN]
        if not fid or not name or fid in seen_ids:
            continue
        seen_ids.add(fid)
        parent_id = str(item.get("parentId")).strip() if item.get("parentId") else None
        qids = list(dict.fromkeys(str(q) for q in (item.get("questionIds") or []) if str(q).strip()))
        created_at = item.get("createdAt")
        if not isinstance(created_at, (int, float)) or created_at <= 0:
            match = re.match(r"^uf_(\d+)", fid)
            created_at = int(match.group(1)) if match else int(time.time() * 1000)
        cleaned = {
            "id": fid,
            "name": name,
            "parentId": parent_id or None,
            "questionIds": qids,
            "createdAt": int(created_at),
        }
        sort_order = _clean_sort_order(item.get("sortOrder"))
        if sort_order is not None:
            cleaned["sortOrder"] = sort_order
        out.append(cleaned)
    return out


def _clean_smart_folders(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen_ids: set[str] = set()
    for item in raw[:MAX_SMART_FOLDERS]:
        if not isinstance(item, dict):
            continue
        fid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()[:MAX_NAME_LEN]
        if not fid or not name or fid in seen_ids:
            continue
        seen_ids.add(fid)
        parent_id = str(item.get("parentId")).strip() if item.get("parentId") else None
        flt = item.get("filter") if isinstance(item.get("filter"), dict) else {}
        created_at = item.get("createdAt")
        if not isinstance(created_at, (int, float)) or created_at <= 0:
            created_at = int(time.time() * 1000)
        cleaned = {
            "id": fid,
            "name": name,
            "parentId": parent_id or None,
            "filter": deepcopy(flt),
            "createdAt": int(created_at),
        }
        sort_order = _clean_sort_order(item.get("sortOrder"))
        if sort_order is not None:
            cleaned["sortOrder"] = sort_order
        out.append(cleaned)
    return out


def _clean_doc_placements(raw) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for key, entry in raw.items():
        doc_id = str(key or "").strip()
        if doc_id.startswith(("paper:", "book:")):
            doc_id = doc_id.split(":", 1)[1].strip()
        if not doc_id or not isinstance(entry, dict):
            continue
        raw_parent = entry.get("parentId")
        parent_id: str | None = None
        if isinstance(raw_parent, str):
            pid = raw_parent.strip()
            if pid.startswith("userfolder:"):
                pid = pid.removeprefix("userfolder:").strip()
            if pid in {"__root__", "root", "section:folders"}:
                parent_id = "section:folders"
            elif pid and pid != "default":
                parent_id = pid
        sort_order = _clean_sort_order(entry.get("sortOrder"))
        if parent_id is None and sort_order is None:
            continue
        cleaned: dict[str, object] = {"parentId": parent_id}
        if sort_order is not None:
            cleaned["sortOrder"] = sort_order
        out[doc_id] = cleaned
    return out


def _clean_custom_tree_docs(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        doc_id = str(item or "").strip()
        if doc_id.startswith(("paper:", "book:")):
            doc_id = doc_id.split(":", 1)[1].strip()
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            out.append(doc_id)
    return out


def _load_unlocked() -> dict:
    path = store_path()
    if not path.is_file():
        return _empty_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    paper_sort = raw.get("paper_sort_mode")
    if paper_sort not in VALID_SORT_MODES:
        paper_sort = DEFAULT_SORT_MODE
    raw_history = raw.get("history")
    history: list = raw_history if isinstance(raw_history, list) else []
    return {
        "version": 1,
        "revision": int(raw.get("revision") or 0),
        "updated_at": str(raw.get("updated_at") or ""),
        "user_folders": _clean_user_folders(raw.get("user_folders")),
        "doc_overrides": _clean_doc_overrides(raw.get("doc_overrides")),
        "doc_placements": _clean_doc_placements(raw.get("doc_placements")),
        "custom_tree_docs": _clean_custom_tree_docs(raw.get("custom_tree_docs")),
        "smart_folders": _clean_smart_folders(raw.get("smart_folders")),
        "sort_modes": _clean_sort_modes(raw.get("sort_modes")),
        "paper_sort_mode": paper_sort,
        "history": [entry for entry in history[-MAX_HISTORY:] if isinstance(entry, dict)],
    }


def _save_unlocked(state: dict) -> dict:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix="library-folders-",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(payload)
        temp_path.replace(path)
    except OSError as exc:
        raise FolderError("文件夹状态暂时无法写入磁盘", 500) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return state


def _paper_type_map() -> dict[str, str]:
    return {str(pk): str(mt) for pk, mt in Paper.objects.values_list("pk", "material_type")}


def _doc_node_id(doc_id: str, doc_types: dict[str, str]) -> str:
    mtype = doc_types.get(doc_id)
    prefix = "book:" if mtype == Paper.MaterialType.BOOK else "paper:"
    return f"{prefix}{doc_id}"


def _resolve_doc_paper(raw_ref: str) -> Paper:
    raw = str(raw_ref or "").strip()
    if raw.startswith(("paper:", "book:")):
        raw = raw.split(":", 1)[1].strip()
    if not raw:
        raise FolderError("请提供试卷或教材编号")
    try:
        doc_uuid = uuid.UUID(raw)
    except ValueError:
        raise FolderError(f"找不到资料：{raw_ref}", 404) from None
    paper = Paper.objects.filter(pk=doc_uuid).first()
    if paper is None:
        raise FolderError(f"找不到资料：{raw_ref}", 404)
    return paper


def _effective_doc_parent(
    doc_id: str, material_type: str, doc_placements: dict[str, dict], valid_uf_ids: set[str], doc_types: dict[str, str]
) -> str:
    default_parent = "group:books" if material_type == Paper.MaterialType.BOOK else "group:papers"
    entry = doc_placements.get(doc_id)
    if not isinstance(entry, dict):
        return default_parent
    pid = str(entry.get("parentId") or "").strip()
    if not pid:
        return default_parent
    if pid in {"__root__", "root", "section:folders"}:
        return "section:folders"
    if pid in {"group:papers", "group:books"}:
        return pid
    if pid.startswith("userfolder:"):
        pid = pid.removeprefix("userfolder:")
    if pid in valid_uf_ids:
        return pid
    if pid.startswith(("paper:", "book:")):
        target_doc_id = pid.split(":", 1)[1]
        if target_doc_id in doc_types and target_doc_id != doc_id:
            return _doc_node_id(target_doc_id, doc_types)
    return default_parent


def _effective_uf_parent(item: dict, valid_uf_ids: set[str], doc_types: dict[str, str]) -> str | None:
    pid = item.get("parentId")
    if not pid:
        return None
    pid_str = str(pid).strip()
    if pid_str.startswith("userfolder:"):
        pid_str = pid_str.removeprefix("userfolder:")
    if pid_str in {"__root__", "root", "section:folders"}:
        return None
    if pid_str in valid_uf_ids:
        return pid_str
    if pid_str in {"group:papers", "group:books"}:
        return pid_str
    if pid_str.startswith(("paper:", "book:")):
        target_doc_id = pid_str.split(":", 1)[1]
        if target_doc_id in doc_types:
            return _doc_node_id(target_doc_id, doc_types)
    return None


def _is_system_parent(parent_id: str | None) -> bool:
    pid = str(parent_id or "")
    return pid in {"group:papers", "group:books"} or pid.startswith(("paper:", "book:"))


def _normalize_parent_id(raw_parent: str | None, user_folders: list[dict]) -> str | None:
    if raw_parent is None:
        return None
    pid = str(raw_parent).strip()
    if not pid or pid in {"__root__", "root", "section:folders"}:
        return None
    if pid.startswith("userfolder:"):
        pid = pid.removeprefix("userfolder:")
    if pid in {"group:papers", "group:books"}:
        return pid
    if pid.startswith(("paper:", "book:")):
        doc_id = pid.split(":", 1)[1]
        try:
            doc_uuid = uuid.UUID(doc_id)
        except ValueError:
            raise FolderError(f"找不到父级资料节点：{pid}") from None
        paper = Paper.objects.filter(pk=doc_uuid).first()
        if paper is None:
            raise FolderError(f"找不到父级资料：{doc_id}", 404)
        prefix = "book:" if paper.material_type == Paper.MaterialType.BOOK else "paper:"
        return prefix + str(paper.pk)
    if any(item["id"] == pid for item in user_folders):
        return pid
    # Also allow resolving by exact unique folder name
    matched = [item for item in user_folders if item["name"] == pid]
    if len(matched) == 1:
        return matched[0]["id"]
    if len(matched) > 1:
        raise FolderError(f"有多个同名父级文件夹「{pid}」，请用编号区分")
    raise FolderError(f"找不到父级文件夹：{raw_parent}", 404)


def _parent_depth(
    parent_id: str | None,
    items: list[dict],
    *,
    is_custom: bool = False,
    doc_placements: dict[str, dict] | None = None,
    doc_types: dict[str, str] | None = None,
) -> int:
    if not parent_id or parent_id in {"__root__", "section:folders", "section:custom"}:
        return 0
    raw_id = str(parent_id)
    if raw_id.startswith("userfolder:"):
        raw_id = raw_id.removeprefix("userfolder:")
    elif raw_id.startswith("custom:"):
        raw_id = raw_id.removeprefix("custom:")
    if not is_custom:
        if raw_id in {"group:papers", "group:books"}:
            return 1
        if raw_id.startswith("bookchap:"):
            return 3
        if raw_id.startswith("booksec:"):
            return 4
    by_id = {item["id"]: item for item in items}
    valid_uf_ids = set(by_id.keys())
    placements = doc_placements or {}
    dtypes = doc_types if doc_types is not None else (_paper_type_map() if not is_custom else {})
    depth = 0
    cursor: str | None = raw_id
    visited: set[str] = set()
    while cursor and cursor not in visited:
        visited.add(cursor)
        if not is_custom:
            if cursor in {"group:papers", "group:books"}:
                return depth + 1
            if cursor == "section:folders":
                return depth
            if cursor.startswith("bookchap:"):
                return depth + 3
            if cursor.startswith("booksec:"):
                return depth + 4
            if cursor.startswith(("paper:", "book:")):
                doc_id = cursor.split(":", 1)[1]
                depth += 1
                mtype = dtypes.get(doc_id)
                if mtype is None:
                    return depth + 1
                cursor = _effective_doc_parent(doc_id, mtype, placements, valid_uf_ids, dtypes)
                continue
        item = by_id.get(cursor)
        if not item:
            break
        depth += 1
        cursor = item.get("parentId")
    return depth


def _subtree_height(
    items: list[dict],
    folder_id: str,
    *,
    doc_placements: dict[str, dict] | None = None,
    doc_types: dict[str, str] | None = None,
    is_custom: bool = False,
) -> int:
    by_parent: dict[str | None, list[str]] = {}
    valid_uf_ids = {item["id"] for item in items}
    for item in items:
        by_parent.setdefault(item.get("parentId") or None, []).append(item["id"])
    if not is_custom and doc_types:
        placements = doc_placements or {}
        for doc_id, mtype in doc_types.items():
            eff_parent = _effective_doc_parent(doc_id, mtype, placements, valid_uf_ids, doc_types)
            by_parent.setdefault(eff_parent, []).append(_doc_node_id(doc_id, doc_types))
    visited: set[str] = set()

    def walk(fid: str) -> int:
        if fid in visited:
            return 0
        visited.add(fid)
        kids = by_parent.get(fid, [])
        return 1 + max((walk(kid) for kid in kids), default=0)

    return max(1, walk(folder_id))


def _is_descendant_or_self(
    items: list[dict],
    source_id: str,
    target_parent_id: str | None,
    *,
    doc_placements: dict[str, dict] | None = None,
    doc_types: dict[str, str] | None = None,
    is_custom: bool = False,
) -> bool:
    if not target_parent_id:
        return False
    src = str(source_id)
    if src == str(target_parent_id):
        return True
    by_id = {item["id"]: item for item in items}
    valid_uf_ids = set(by_id.keys())
    placements = doc_placements or {}
    dtypes = doc_types if doc_types is not None else {}
    cursor: str | None = str(target_parent_id)
    visited: set[str] = set()
    while cursor and cursor not in visited:
        if cursor == src:
            return True
        visited.add(cursor)
        if not is_custom and cursor.startswith(("paper:", "book:")):
            doc_id = cursor.split(":", 1)[1]
            mtype = dtypes.get(doc_id)
            if mtype is None:
                break
            cursor = _effective_doc_parent(doc_id, mtype, placements, valid_uf_ids, dtypes)
            continue
        if cursor in {"section:folders", "group:papers", "group:books"}:
            break
        item = by_id.get(cursor)
        cursor = item.get("parentId") if item else None
    return False


def _collect_subtree_ids(items: list[dict], root_id: str) -> set[str]:
    by_parent: dict[str | None, list[str]] = {}
    for item in items:
        by_parent.setdefault(item.get("parentId") or None, []).append(item["id"])
    result: set[str] = set()
    stack = [root_id]
    while stack:
        cur = stack.pop()
        if cur in result:
            continue
        result.add(cur)
        stack.extend(by_parent.get(cur, []))
    return result


def _find_user_folder(user_folders: list[dict], ref: str) -> dict:
    raw = str(ref or "").strip()
    if raw.startswith("userfolder:"):
        raw = raw.removeprefix("userfolder:")
    if not raw:
        raise FolderError("请提供文件夹编号或名称")
    for item in user_folders:
        if item["id"] == raw:
            return item
    by_name = [item for item in user_folders if item["name"] == raw]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise FolderError(f"有多个同名文件夹「{raw}」，请用编号（{by_name[0]['id']} 等）区分")
    raise FolderError(f"找不到文件夹：{ref}", 404)


def _find_smart_folder(smart_folders: list[dict], ref: str) -> dict:
    raw = str(ref or "").strip()
    if raw.startswith("custom:"):
        raw = raw.removeprefix("custom:")
    if not raw:
        raise FolderError("请提供智能文件夹编号或名称")
    for item in smart_folders:
        if item["id"] == raw:
            return item
    by_name = [item for item in smart_folders if item["name"] == raw]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise FolderError(f"有多个同名智能文件夹「{raw}」，请用编号区分")
    raise FolderError(f"找不到智能文件夹：{ref}", 404)


def _resolve_publication_ids(raw_ids) -> list[str]:
    if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > MAX_BATCH_QUESTIONS:
        raise FolderError(f"请提供 1–{MAX_BATCH_QUESTIONS} 道题目编号（UUID 或 Q-xxxxxxxx）")
    resolved: list[str] = []
    seen: set[str] = set()
    for item in raw_ids:
        text = str(item or "").strip()
        if not text:
            raise FolderError("题目编号不能为空")
        try:
            pub = library.resolve_publication_ref(text)
        except ValueError as exc:
            raise FolderError(str(exc), 404) from None
        if pub.status != PublishedQuestion.Status.PUBLISHED:
            raise FolderError(f"题目 {pub.id} 已下架或被新版替代，不能整理进文件夹")
        pid = str(pub.pk)
        if pid not in seen:
            seen.add(pid)
            resolved.append(pid)
    return resolved


def _snapshot(state: dict) -> dict:
    return {
        "user_folders": deepcopy(state["user_folders"]),
        "doc_overrides": deepcopy(state["doc_overrides"]),
        "doc_placements": deepcopy(state["doc_placements"]),
        "custom_tree_docs": deepcopy(state["custom_tree_docs"]),
        "smart_folders": deepcopy(state["smart_folders"]),
        "sort_modes": deepcopy(state["sort_modes"]),
        "paper_sort_mode": state["paper_sort_mode"],
    }


def _record_history(state: dict, action: str, summary: str, before: dict, *, extra: dict | None = None) -> dict:
    op = {
        "id": str(uuid.uuid4()),
        "action": action,
        "summary": summary,
        "created_at": timezone.now().isoformat(),
        "before": before,
        "undone_at": None,
    }
    if extra:
        op.update(extra)
    state["history"].append(op)
    if len(state["history"]) > MAX_HISTORY:
        state["history"] = state["history"][-MAX_HISTORY:]
    return op


def _parse_book_section_parts(raw_section: str) -> tuple[str, str, bool]:
    raw = str(raw_section or "").strip()
    sep = raw.find(" · ")
    if sep <= 0:
        return "", raw, False
    left = raw[:sep].strip()
    right = raw[sep + 3 :].strip()
    cleaned_sub = re.sub(r"\s*(?:第\s*)?[\d０-９]+(?:\s*[-—~～]\s*[\d０-９]+)?\s*$", "", right)
    cleaned_sub = re.sub(r"\s*[（(]\s*[\d０-９一二三四五六七八九十]+\s*[)）]\s*$", "", cleaned_sub).strip()
    return left, (cleaned_sub or right), bool(left)


def _build_tree(state: dict) -> dict:
    user_folders = state["user_folders"]
    sort_modes = state["sort_modes"]
    global_sort = state["paper_sort_mode"]
    doc_overrides = state["doc_overrides"]
    doc_placements = state.get("doc_placements") or {}
    custom_tree_set = set(state.get("custom_tree_docs") or [])

    all_papers = list(Paper.objects.order_by("-created_at"))
    doc_types: dict[str, str] = {str(p.pk): str(p.material_type) for p in all_papers}
    paper_by_id: dict[str, Paper] = {str(p.pk): p for p in all_papers}

    # Published questions per paper for counts & book chapter trees
    pub_rows = list(
        PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).values(
            "id", "paper_id", "number", "content"
        )
    )
    pubs_by_paper: dict[str, list[dict]] = {}
    for row in pub_rows:
        pid = str(row["paper_id"]) if row["paper_id"] else ""
        if pid:
            pubs_by_paper.setdefault(pid, []).append(row)

    known_uf_ids = {item["id"] for item in user_folders}
    uf_by_id = {item["id"]: item for item in user_folders}
    by_parent: dict[str, list[dict]] = {}
    for item in user_folders:
        eff_pid = _effective_uf_parent(item, known_uf_ids, doc_types)
        key = eff_pid if eff_pid else "__root__"
        by_parent.setdefault(key, []).append(item)

    placed_docs_by_parent: dict[str, list[str]] = {}
    doc_effective_parent: dict[str, str] = {}
    for doc_id, mtype in doc_types.items():
        eff_parent = _effective_doc_parent(doc_id, mtype, doc_placements, known_uf_ids, doc_types)
        doc_effective_parent[doc_id] = eff_parent
        placed_docs_by_parent.setdefault(eff_parent, []).append(doc_id)

    def direct_doc_qids(doc_id: str) -> set[str]:
        ov = doc_overrides.get(doc_id) or {"added": [], "removed": []}
        base_ids = {str(row["id"]) for row in pubs_by_paper.get(doc_id, [])}
        base_ids.update(str(x) for x in (ov.get("added") or []))
        base_ids.difference_update(str(x) for x in (ov.get("removed") or []))
        return base_ids

    def doc_subtree_qids(doc_id: str, visited: set[str] | None = None) -> set[str]:
        seen = set() if visited is None else visited
        node_key = _doc_node_id(doc_id, doc_types)
        if node_key in seen:
            return set()
        seen.add(node_key)
        qset = direct_doc_qids(doc_id)
        for uf in by_parent.get(node_key, []):
            qset.update(uf_subtree_qids(uf["id"], seen))
        for child_doc_id in placed_docs_by_parent.get(node_key, []):
            qset.update(doc_subtree_qids(child_doc_id, seen))
        return qset

    def uf_subtree_qids(fid: str, visited: set[str] | None = None) -> set[str]:
        seen = set() if visited is None else visited
        if fid in seen:
            return set()
        seen.add(fid)
        item = uf_by_id.get(fid)
        qset: set[str] = set(item.get("questionIds") or []) if item else set()
        for child in by_parent.get(fid, []):
            qset.update(uf_subtree_qids(child["id"], seen))
        for child_doc_id in placed_docs_by_parent.get(fid, []):
            qset.update(doc_subtree_qids(child_doc_id, seen))
        return qset

    def _sort_manual_items(items: list[dict], sort_key: str) -> list[dict]:
        if sort_modes.get(sort_key) != "manual":
            return items
        return sorted(
            items,
            key=lambda x: (x.get("sort_order") is None, x.get("sort_order") if x.get("sort_order") is not None else 0),
        )

    def build_placed_doc_brief(doc_id: str, visited: set[str] | None = None) -> dict | None:
        paper = paper_by_id.get(doc_id)
        if paper is None:
            return None
        seen = set() if visited is None else visited
        node_id = _doc_node_id(doc_id, doc_types)
        if node_id in seen:
            return None
        seen.add(node_id)
        placement = doc_placements.get(doc_id) or {}
        subfolders = [build_uf_node(child, seen) for child in by_parent.get(node_id, [])]
        child_docs = []
        for child_doc_id in placed_docs_by_parent.get(node_id, []):
            brief = build_placed_doc_brief(child_doc_id, seen)
            if brief is not None:
                child_docs.append(brief)
        return {
            "id": doc_id,
            "node_id": node_id,
            "name": paper.display_name,
            "material_type": paper.material_type,
            "custom_tree": doc_id in custom_tree_set,
            "folder_count": len(doc_subtree_qids(doc_id)),
            "sort_mode": sort_modes.get(node_id, ""),
            "sort_order": placement.get("sortOrder"),
            "effective_parent": doc_effective_parent.get(doc_id),
            "subfolders": _sort_manual_items(subfolders, node_id),
            "placed_docs": _sort_manual_items(child_docs, node_id),
        }

    def build_uf_node(item: dict, visited: set[str] | None = None) -> dict:
        seen = set() if visited is None else visited
        node_id = f"userfolder:{item['id']}"
        if item["id"] in seen:
            return {
                "id": item["id"],
                "node_id": node_id,
                "name": item["name"],
                "parent_id": item.get("parentId"),
                "direct_count": len(item.get("questionIds") or []),
                "count": 0,
                "question_ids": list(item.get("questionIds") or []),
                "sort_mode": sort_modes.get(node_id, ""),
                "sort_order": item.get("sortOrder"),
                "children": [],
                "placed_docs": [],
            }
        next_seen = seen | {item["id"]}
        kids = [build_uf_node(child, next_seen) for child in by_parent.get(item["id"], [])]
        placed_docs = []
        for doc_id in placed_docs_by_parent.get(item["id"], []):
            brief = build_placed_doc_brief(doc_id, set(next_seen))
            if brief is not None:
                placed_docs.append(brief)
        return {
            "id": item["id"],
            "node_id": node_id,
            "name": item["name"],
            "parent_id": item.get("parentId"),
            "direct_count": len(item.get("questionIds") or []),
            "count": len(uf_subtree_qids(item["id"])),
            "question_ids": list(item.get("questionIds") or []),
            "sort_mode": sort_modes.get(node_id, ""),
            "sort_order": item.get("sortOrder"),
            "children": _sort_manual_items(kids, node_id),
            "placed_docs": _sort_manual_items(placed_docs, node_id),
        }

    papers_out = []
    books_out = []
    for paper in all_papers:
        pid = str(paper.pk)
        pub_list = pubs_by_paper.get(pid, [])
        ov = doc_overrides.get(pid) or {"added": [], "removed": []}
        published_count = max(0, len(pub_list) + len(ov["added"]) - len(ov["removed"]))
        node_id = _doc_node_id(pid, doc_types)
        subfolders = _sort_manual_items([build_uf_node(child) for child in by_parent.get(node_id, [])], node_id)
        placed_docs = []
        for child_doc_id in placed_docs_by_parent.get(node_id, []):
            brief = build_placed_doc_brief(child_doc_id, {node_id})
            if brief is not None:
                placed_docs.append(brief)
        placement = doc_placements.get(pid)
        is_custom_tree = pid in custom_tree_set
        entry = {
            "id": pid,
            "node_id": node_id,
            "name": paper.display_name,
            "filename": paper.filename,
            "material_type": paper.material_type,
            "status": paper.status,
            "published": len(pub_list),
            "folder_count": max(published_count, len(doc_subtree_qids(pid))),
            "sort_mode": sort_modes.get(node_id, ""),
            "sort_order": placement.get("sortOrder") if isinstance(placement, dict) else None,
            "custom_tree": is_custom_tree,
            "placement": placement,
            "effective_parent": doc_effective_parent.get(pid),
            "subfolders": subfolders,
            "placed_docs": _sort_manual_items(placed_docs, node_id),
        }
        if paper.material_type == Paper.MaterialType.BOOK:
            if is_custom_tree:
                entry["sections"] = []
            else:
                # Aggregate chapters / sections from PublishedQuestion.content["section"]
                sec_groups: dict[str, dict[str, int]] = {}
                flat_sections: dict[str, int] = {}
                has_compound = False
                for row in pub_list:
                    sec = str((row.get("content") or {}).get("section") or "").strip()
                    grp, sub, is_comp = _parse_book_section_parts(sec)
                    if is_comp:
                        has_compound = True
                    flat_sections[sec or "（未标注小节）"] = flat_sections.get(sec or "（未标注小节）", 0) + 1
                    grp_key = grp if is_comp else (sec or "（未标注小节）")
                    sub_key = sub if is_comp else ""
                    bucket = sec_groups.setdefault(grp_key, {})
                    bucket[sub_key] = bucket.get(sub_key, 0) + 1
                if has_compound:
                    entry["sections"] = [
                        {
                            "name": grp_name,
                            "count": sum(subs.values()),
                            "children": [
                                {"name": sname or "（未标注栏目）", "count": cnt} for sname, cnt in subs.items()
                            ],
                        }
                        for grp_name, subs in sec_groups.items()
                    ]
                else:
                    entry["sections"] = [
                        {"name": sname, "count": cnt, "children": []} for sname, cnt in flat_sections.items()
                    ]
            books_out.append(entry)
        else:
            papers_out.append(entry)

    smart_folders = state["smart_folders"]
    smart_by_parent: dict[str, list[dict]] = {}
    smart_ids = {item["id"] for item in smart_folders}
    for item in smart_folders:
        pid = item.get("parentId")
        key = pid if (pid and pid in smart_ids) else "__root__"
        smart_by_parent.setdefault(key, []).append(item)

    def build_smart_node(item: dict) -> dict:
        return {
            "id": item["id"],
            "node_id": f"custom:{item['id']}",
            "name": item["name"],
            "parent_id": item.get("parentId"),
            "filter": item.get("filter") or {},
            "sort_mode": sort_modes.get(f"custom:{item['id']}", ""),
            "sort_order": item.get("sortOrder"),
            "children": [build_smart_node(child) for child in smart_by_parent.get(item["id"], [])],
        }

    from . import library_tag_groups

    root_placed_docs = []
    for doc_id in placed_docs_by_parent.get("section:folders", []):
        brief = build_placed_doc_brief(doc_id)
        if brief is not None:
            root_placed_docs.append(brief)

    return {
        "global_sort_mode": global_sort,
        "sort_modes": dict(sort_modes),
        "user_folders": _sort_manual_items(
            [build_uf_node(item) for item in by_parent.get("__root__", [])], "section:folders"
        ),
        "root_placed_docs": _sort_manual_items(root_placed_docs, "section:folders"),
        "papers_group_subfolders": _sort_manual_items(
            [build_uf_node(item) for item in by_parent.get("group:papers", [])], "group:papers"
        ),
        "books_group_subfolders": _sort_manual_items(
            [build_uf_node(item) for item in by_parent.get("group:books", [])], "group:books"
        ),
        "papers": papers_out,
        "books": books_out,
        "smart_folders": [build_smart_node(item) for item in smart_by_parent.get("__root__", [])],
        "tag_groups": library_tag_groups.list_groups_data(),
        "tag_summary": {
            "knowledge": LibraryTag.objects.filter(kind=LibraryTag.Kind.KNOWLEDGE, active=True).count(),
            "method": LibraryTag.objects.filter(kind=LibraryTag.Kind.METHOD, active=True).count(),
            "custom": LibraryTag.objects.filter(kind=LibraryTag.Kind.CUSTOM, active=True).count(),
        },
    }


def _public_operations(history: list[dict]) -> list[dict]:
    return [
        {
            "id": op["id"],
            "action": op["action"],
            "summary": op.get("summary", ""),
            "created_at": op.get("created_at", ""),
            "undone": bool(op.get("undone_at")),
            "undone_at": op.get("undone_at"),
        }
        for op in reversed(history[-20:])
    ]


def overview(*, include_tree: bool = True) -> dict:
    with _LOCK:
        state = _load_unlocked()
    data = {
        "ok": True,
        "revision": state["revision"],
        "updated_at": state["updated_at"],
        "user_folders": state["user_folders"],
        "doc_overrides": state["doc_overrides"],
        "doc_placements": state["doc_placements"],
        "custom_tree_docs": state["custom_tree_docs"],
        "smart_folders": state["smart_folders"],
        "sort_modes": state["sort_modes"],
        "paper_sort_mode": state["paper_sort_mode"],
        "operations": _public_operations(state["history"]),
    }
    if include_tree:
        data["tree"] = _build_tree(state)
    return data


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(2)}"


def _collect_uf_only_subtree_ids(items: list[dict], root_parent_id: str | None) -> set[str]:
    """Collect user_folder IDs strictly under root_parent_id (stopping at paper:/book: boundaries)."""
    by_parent: dict[str | None, list[str]] = {}
    for item in items:
        by_parent.setdefault(item.get("parentId") or None, []).append(item["id"])
    result: set[str] = set()
    stack = list(by_parent.get(root_parent_id, []))
    while stack:
        cur = stack.pop()
        if cur in result:
            continue
        result.add(cur)
        stack.extend(by_parent.get(cur, []))
    return result


def _resolve_doc_move_target(raw_parent, user_folders: list[dict]) -> tuple[str | None, str]:
    """Return (stored_parent_id, effective_parent_for_depth_check)."""
    if raw_parent is None:
        return None, ""
    pid = str(raw_parent).strip()
    if not pid or pid == "default":
        return None, ""
    if pid in {"__root__", "root", "section:folders"}:
        return "section:folders", "section:folders"
    norm = _normalize_parent_id(pid, user_folders)
    if norm is None:
        return "section:folders", "section:folders"
    return norm, norm


def _mutate_move_doc(
    state: dict, payload: dict, doc_types: dict[str, str], before: dict
) -> tuple[str, dict | None, bool]:
    raw_doc = payload.get("doc_id") or payload.get("folder_id") or payload.get("paper_id") or ""
    paper = _resolve_doc_paper(str(raw_doc))
    doc_uuid = str(paper.pk)
    doc_node_id = _doc_node_id(doc_uuid, doc_types)
    default_group = "group:books" if paper.material_type == Paper.MaterialType.BOOK else "group:papers"

    stored_parent, check_parent = _resolve_doc_move_target(payload.get("parent_id"), state["user_folders"])
    if not check_parent:
        check_parent = default_group

    if stored_parent == default_group:
        stored_parent = None

    has_sort_order = "sort_order" in payload
    new_sort_order = _clean_sort_order(payload.get("sort_order")) if has_sort_order else None

    cur_entry = state["doc_placements"].get(doc_uuid) or {}
    cur_parent = cur_entry.get("parentId")
    cur_sort = cur_entry.get("sortOrder")

    if cur_parent == stored_parent and (not has_sort_order or cur_sort == new_sort_order):
        return "资料已在目标位置中", None, False

    if stored_parent:
        if _is_descendant_or_self(
            state["user_folders"],
            doc_node_id,
            stored_parent,
            doc_placements=state["doc_placements"],
            doc_types=doc_types,
        ):
            raise FolderError("不能将资料移动到自身或其子文件夹下")

    parent_depth = _parent_depth(
        check_parent, state["user_folders"], doc_placements=state["doc_placements"], doc_types=doc_types
    )
    height = _subtree_height(
        state["user_folders"], doc_node_id, doc_placements=state["doc_placements"], doc_types=doc_types
    )
    if parent_depth + height > MAX_FOLDER_DEPTH:
        raise FolderError(f"移动后文件夹总层级将超过 {MAX_FOLDER_DEPTH} 层上限")

    next_sort = new_sort_order if has_sort_order else cur_sort
    if stored_parent is None and next_sort is None:
        state["doc_placements"].pop(doc_uuid, None)
    else:
        entry: dict[str, object] = {"parentId": stored_parent}
        if next_sort is not None:
            entry["sortOrder"] = next_sort
        state["doc_placements"][doc_uuid] = entry

    op = _record_history(state, "move_doc", f"移动资料「{paper.display_name}」", before)
    return f"已移动资料「{paper.display_name}」", op, True


def _mutate_reorder(state: dict, payload: dict, doc_types: dict[str, str], before: dict) -> tuple[str, dict]:
    kind = str(payload.get("kind") or "folder").strip()
    order = payload.get("order")
    if not isinstance(order, list) or not order:
        raise FolderError("请提供非空的排序列表 order")
    set_manual = bool(payload.get("set_manual", True))

    if kind == "smart":
        raw_parent = payload.get("parent_id")
        parent_id = None
        if raw_parent and str(raw_parent).strip() not in {"__root__", "root", "section:custom"}:
            parent_id = _find_smart_folder(state["smart_folders"], str(raw_parent))["id"]
        parent_sort_key = f"custom:{parent_id}" if parent_id else "section:custom"
        siblings = [s for s in state["smart_folders"] if (s.get("parentId") or None) == parent_id]
        if not siblings:
            raise FolderError("目标智能文件夹下没有可排序的子项")
        siblings_sorted = sorted(
            siblings,
            key=lambda s: (
                s.get("sortOrder") is None,
                s.get("sortOrder") if s.get("sortOrder") is not None else 0,
                s.get("createdAt") or 0,
            ),
        )
        by_id = {s["id"]: s for s in siblings}
        by_name: dict[str, list[dict]] = {}
        for s in siblings:
            by_name.setdefault(s["name"], []).append(s)

        ordered_items: list[dict] = []
        seen_ids: set[str] = set()
        for raw_token in order:
            token = str(raw_token or "").strip()
            if token.startswith("custom:"):
                token = token.removeprefix("custom:")
            matched = by_id.get(token)
            if matched is None:
                cands = by_name.get(token, [])
                if len(cands) == 1:
                    matched = cands[0]
                elif len(cands) > 1:
                    raise FolderError(f"同级存在多个同名智能文件夹「{token}」，请用编号区分")
            if matched is None:
                raise FolderError(f"排序项「{raw_token}」不属于目标父级下的直接子节点")
            if matched["id"] in seen_ids:
                raise FolderError(f"排序列表中存在重复项：{raw_token}")
            seen_ids.add(matched["id"])
            ordered_items.append(matched)

        for s in siblings_sorted:
            if s["id"] not in seen_ids:
                ordered_items.append(s)
        for idx, s in enumerate(ordered_items):
            s["sortOrder"] = idx * 10
        if set_manual:
            state["sort_modes"][parent_sort_key] = "manual"
        op = _record_history(state, "reorder", f"调整智能文件夹排序（{len(ordered_items)} 项）", before)
        return f"已调整 {len(ordered_items)} 个智能文件夹的排序", op

    if kind != "folder":
        raise FolderError("kind 只能是 folder 或 smart")

    raw_parent = payload.get("parent_id")
    if raw_parent is None or str(raw_parent).strip() in {"", "__root__", "root", "section:folders"}:
        uf_match_parent = None
        doc_match_parent = "section:folders"
        parent_key = "section:folders"
    else:
        uf_match_parent = _normalize_parent_id(str(raw_parent), state["user_folders"])
        if uf_match_parent is None:
            doc_match_parent = "section:folders"
            parent_key = "section:folders"
        elif _is_system_parent(uf_match_parent):
            doc_match_parent = uf_match_parent
            parent_key = uf_match_parent
        else:
            doc_match_parent = uf_match_parent
            parent_key = f"userfolder:{uf_match_parent}"

    valid_uf_ids = {f["id"] for f in state["user_folders"]}
    sibling_ufs = [
        f for f in state["user_folders"] if _effective_uf_parent(f, valid_uf_ids, doc_types) == uf_match_parent
    ]
    paper_map = {str(p.pk): p for p in Paper.objects.all()}
    sibling_docs = [
        doc_id
        for doc_id, mtype in doc_types.items()
        if _effective_doc_parent(doc_id, mtype, state["doc_placements"], valid_uf_ids, doc_types) == doc_match_parent
    ]

    if not sibling_ufs and not sibling_docs:
        raise FolderError("目标父级下没有可排序的子文件夹或资料")

    # Build canonical key ("uf:<id>" or "doc:<uuid>") for each direct sibling
    entries: list[tuple[str, str, dict | str, int | None, int]] = []
    uf_by_id: dict[str, tuple[str, dict]] = {}
    uf_by_name: dict[str, list[tuple[str, dict]]] = {}
    for uf in sibling_ufs:
        key = f"uf:{uf['id']}"
        so = uf.get("sortOrder")
        entries.append((key, "uf", uf, so, uf.get("createdAt") or 0))
        uf_by_id[uf["id"]] = (key, uf)
        uf_by_name.setdefault(uf["name"], []).append((key, uf))

    doc_by_id: dict[str, tuple[str, str]] = {}
    doc_by_name: dict[str, list[tuple[str, str]]] = {}
    for doc_id in sibling_docs:
        key = f"doc:{doc_id}"
        placement = state["doc_placements"].get(doc_id) or {}
        so = placement.get("sortOrder")
        p = paper_map.get(doc_id)
        created_ms = int(p.created_at.timestamp() * 1000) if (p and p.created_at) else 0
        entries.append((key, "doc", doc_id, so, created_ms))
        doc_by_id[doc_id] = (key, doc_id)
        if p:
            doc_by_name.setdefault(p.display_name, []).append((key, doc_id))

    entries.sort(key=lambda row: (row[3] is None, row[3] if row[3] is not None else 0, row[4]))
    entry_map = {row[0]: row for row in entries}

    ordered_keys: list[str] = []
    seen_keys: set[str] = set()
    for raw_token in order:
        token = str(raw_token or "").strip()
        resolved_key: str | None = None
        if token.startswith("userfolder:"):
            uf_id = token.removeprefix("userfolder:")
            if uf_id in uf_by_id:
                resolved_key = uf_by_id[uf_id][0]
        elif token.startswith(("paper:", "book:")):
            did = token.split(":", 1)[1]
            if did in doc_by_id:
                resolved_key = doc_by_id[did][0]
        elif token in uf_by_id:
            resolved_key = uf_by_id[token][0]
        elif token in doc_by_id:
            resolved_key = doc_by_id[token][0]
        else:
            uf_cands = uf_by_name.get(token, [])
            doc_cands = doc_by_name.get(token, [])
            if len(uf_cands) + len(doc_cands) == 1:
                resolved_key = (uf_cands + doc_cands)[0][0]
            elif len(uf_cands) + len(doc_cands) > 1:
                raise FolderError(f"同级存在多个同名项目「{token}」，请用节点 ID 区分")
        if resolved_key is None:
            raise FolderError(f"排序项「{raw_token}」不属于目标父级下的直接子节点")
        if resolved_key in seen_keys:
            raise FolderError(f"排序列表中存在重复项：{raw_token}")
        seen_keys.add(resolved_key)
        ordered_keys.append(resolved_key)

    for row in entries:
        if row[0] not in seen_keys:
            ordered_keys.append(row[0])

    for idx, key in enumerate(ordered_keys):
        _, kind_tag, obj, _, _ = entry_map[key]
        if kind_tag == "uf" and isinstance(obj, dict):
            obj["sortOrder"] = idx * 10
        else:
            doc_id = str(obj)
            cur = state["doc_placements"].get(doc_id)
            if cur is None:
                default_parent = None
                if doc_match_parent not in {"group:papers", "group:books"}:
                    default_parent = doc_match_parent
                state["doc_placements"][doc_id] = {"parentId": default_parent, "sortOrder": idx * 10}
            else:
                cur["sortOrder"] = idx * 10

    if set_manual:
        state["sort_modes"][parent_key] = "manual"
    op = _record_history(state, "reorder", f"调整文件夹排序（{parent_key}，{len(ordered_keys)} 项）", before)
    return f"已调整 {len(ordered_keys)} 个子项的排序", op


def _mutate_apply_tree(state: dict, payload: dict, doc_types: dict[str, str], before: dict) -> tuple[dict, dict | None]:
    raw_root = payload.get("root")
    root_parent_id = _normalize_parent_id(raw_root, state["user_folders"])
    root_sort_key = (
        "section:folders"
        if root_parent_id is None
        else (root_parent_id if _is_system_parent(root_parent_id) else f"userfolder:{root_parent_id}")
    )

    mode = str(payload.get("mode") or "replace").strip()
    if mode not in {"replace", "merge"}:
        raise FolderError("mode 只能是 replace 或 merge")

    raw_tree = payload.get("tree")
    if not isinstance(raw_tree, list):
        raise FolderError("tree 必须是数组")

    takeover_raw = payload.get("takeover")
    if takeover_raw is None:
        takeover = bool(root_parent_id and str(root_parent_id).startswith("book:"))
    else:
        takeover = bool(takeover_raw)

    set_manual_sort = bool(payload.get("set_manual_sort", True))
    sync_sections = bool(payload.get("sync_sections", False))
    dry_run = bool(payload.get("dry_run", False))

    old_subtree_ids = _collect_uf_only_subtree_ids(state["user_folders"], root_parent_id)
    existing_outside = [deepcopy(f) for f in state["user_folders"] if f["id"] not in old_subtree_ids]
    outside_ids = {f["id"] for f in existing_outside}
    old_by_id = {f["id"]: f for f in state["user_folders"] if f["id"] in old_subtree_ids}
    old_by_parent_name: dict[tuple[str | None, str], list[dict]] = {}
    for f in state["user_folders"]:
        if f["id"] in old_subtree_ids:
            old_by_parent_name.setdefault((f.get("parentId") or None, f["name"]), []).append(f)

    # One bulk query to resolve all published questions (UUID or Q-xxxxxxxx short ID)
    pub_pks = list(
        PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).values_list("pk", flat=True)
    )
    pub_by_str = {str(pk): str(pk) for pk in pub_pks}
    pub_by_short = {library.short_id_for(pk): str(pk) for pk in pub_pks}

    def resolve_pub_token(raw_q) -> str:
        text = str(raw_q or "").strip()
        if not text:
            raise FolderError("题目编号不能为空")
        if text in pub_by_str:
            return pub_by_str[text]
        if text.lower().startswith("q-"):
            norm_short = "Q-" + text[2:].lower()
            if norm_short in pub_by_short:
                return pub_by_short[norm_short]
        raise FolderError(f"找不到已入库题目：{raw_q}", 404)

    root_depth = _parent_depth(
        root_parent_id, state["user_folders"], doc_placements=state["doc_placements"], doc_types=doc_types
    )

    reused_ids: set[str] = set()
    used_new_ids: set[str] = set()
    new_tree_folders: list[dict] = []
    doc_assignments: list[tuple[str, str, int]] = []
    seen_docs: set[str] = set()
    section_mapping: dict[str, str] = {}
    folders_with_kids: list[str] = []
    stats = {
        "folders_created": 0,
        "folders_updated": 0,
        "folders_removed": 0,
        "questions_assigned": 0,
        "docs_moved": 0,
        "sections_to_update": 0,
        "max_depth_used": root_depth,
    }
    total_q_refs = 0
    now_ms = int(time.time() * 1000)

    def walk(nodes: list, parent_id: str | None, parent_depth: int, path_parts: list[str]) -> list[dict]:
        nonlocal total_q_refs
        preview_nodes: list[dict] = []
        for idx, raw_node in enumerate(nodes):
            if not isinstance(raw_node, dict):
                raise FolderError("tree 中的每个节点必须是 JSON 对象")
            depth = parent_depth + 1
            if depth > MAX_FOLDER_DEPTH:
                raise FolderError(f"目录层级超过 {MAX_FOLDER_DEPTH} 层上限")
            if depth > stats["max_depth_used"]:
                stats["max_depth_used"] = depth

            name = _clean_name(raw_node.get("name"))
            cur_path = path_parts + [name]

            req_id_raw = raw_node.get("id")
            fid = ""
            created_at = now_ms
            if isinstance(req_id_raw, str) and req_id_raw.strip():
                req_id = req_id_raw.strip().removeprefix("userfolder:")
                if req_id in used_new_ids or req_id in outside_ids:
                    raise FolderError(f"文件夹编号冲突或已在其他分支使用：{req_id}")
                fid = req_id
                if req_id in old_by_id:
                    reused_ids.add(req_id)
                    stats["folders_updated"] += 1
                    created_at = old_by_id[req_id]["createdAt"]
                else:
                    stats["folders_created"] += 1
            else:
                candidates = old_by_parent_name.get((parent_id, name), [])
                matched = next(
                    (c for c in candidates if c["id"] not in reused_ids and c["id"] not in used_new_ids), None
                )
                if matched is not None:
                    fid = matched["id"]
                    reused_ids.add(fid)
                    stats["folders_updated"] += 1
                    created_at = matched["createdAt"]
                else:
                    fid = _gen_id("uf")
                    while fid in used_new_ids or fid in outside_ids or fid in old_by_id:
                        fid = _gen_id("uf")
                    stats["folders_created"] += 1
            used_new_ids.add(fid)

            raw_questions = raw_node.get("questions") or []
            if not isinstance(raw_questions, list):
                raise FolderError(f"节点「{name}」的 questions 必须是数组")
            qids: list[str] = []
            q_seen: set[str] = set()
            for qref in raw_questions:
                total_q_refs += 1
                if total_q_refs > MAX_TREE_QUESTIONS:
                    raise FolderError(f"单次整树提交最多支持 {MAX_TREE_QUESTIONS} 条题目归档")
                pub_id = resolve_pub_token(qref)
                if pub_id not in q_seen:
                    q_seen.add(pub_id)
                    qids.append(pub_id)
                    stats["questions_assigned"] += 1
                if sync_sections:
                    section_mapping.setdefault(pub_id, " / ".join(cur_path)[:120])

            raw_children = raw_node.get("children") or []
            if not isinstance(raw_children, list):
                raise FolderError(f"节点「{name}」的 children 必须是数组")

            raw_docs = raw_node.get("docs") or []
            if not isinstance(raw_docs, list):
                raise FolderError(f"节点「{name}」的 docs 必须是数组")

            sort_order = _clean_sort_order(raw_node.get("sortOrder"))
            if sort_order is None:
                sort_order = idx * 10

            folder_obj = {
                "id": fid,
                "name": name,
                "parentId": parent_id,
                "questionIds": qids,
                "createdAt": created_at,
                "sortOrder": sort_order,
            }
            new_tree_folders.append(folder_obj)

            child_preview = walk(raw_children, fid, depth, cur_path)

            doc_previews: list[dict] = []
            for d_idx, dref in enumerate(raw_docs):
                paper = _resolve_doc_paper(str(dref))
                doc_uuid = str(paper.pk)
                doc_node_id = _doc_node_id(doc_uuid, doc_types)
                if doc_uuid in seen_docs:
                    raise FolderError(f"资料在目录树中重复出现：{paper.display_name}")
                if root_parent_id and (
                    doc_node_id == root_parent_id
                    or _is_descendant_or_self(
                        state["user_folders"],
                        doc_node_id,
                        root_parent_id,
                        doc_placements=state["doc_placements"],
                        doc_types=doc_types,
                    )
                ):
                    raise FolderError(f"不能将资料移动到自身或其子文件夹下：{paper.display_name}")
                doc_height = _subtree_height(
                    state["user_folders"], doc_node_id, doc_placements=state["doc_placements"], doc_types=doc_types
                )
                if depth + doc_height > MAX_FOLDER_DEPTH:
                    raise FolderError(
                        f"资料「{paper.display_name}」放入「{name}」后层级将超过 {MAX_FOLDER_DEPTH} 层上限"
                    )
                if depth + doc_height > stats["max_depth_used"]:
                    stats["max_depth_used"] = depth + doc_height
                seen_docs.add(doc_uuid)
                doc_so = (len(raw_children) + d_idx) * 10
                doc_assignments.append((doc_uuid, fid, doc_so))
                stats["docs_moved"] += 1
                doc_previews.append({"id": doc_uuid, "node_id": doc_node_id, "name": paper.display_name})

            if raw_children or raw_docs:
                folders_with_kids.append(f"userfolder:{fid}")

            preview_nodes.append(
                {"id": fid, "name": name, "question_count": len(qids), "docs": doc_previews, "children": child_preview}
            )
        return preview_nodes

    preview_tree = walk(raw_tree, root_parent_id, root_depth, [])

    removed_ids = (old_subtree_ids - reused_ids) if mode == "replace" else set()
    kept_old_subtree = (
        [deepcopy(f) for f in state["user_folders"] if f["id"] in old_subtree_ids and f["id"] not in reused_ids]
        if mode == "merge"
        else []
    )
    stats["folders_removed"] = len(removed_ids)

    if len(existing_outside) + len(kept_old_subtree) + len(new_tree_folders) > MAX_FOLDERS:
        raise FolderError(f"应用后自建文件夹总数将超过 {MAX_FOLDERS} 个上限")

    if sync_sections:
        stats["sections_to_update"] = len(section_mapping)

    if dry_run:
        return (
            {
                "ok": True,
                "dry_run": True,
                "root": root_sort_key,
                "mode": mode,
                "takeover": takeover,
                "summary": stats,
                "preview_tree": preview_tree,
                "message": (
                    f"预览：将新建 {stats['folders_created']} 个、更新 {stats['folders_updated']} 个、"
                    f"移除 {stats['folders_removed']} 个文件夹，归档 {stats['questions_assigned']} 题"
                ),
            },
            None,
        )

    state["user_folders"] = existing_outside + kept_old_subtree + new_tree_folders
    for fid in removed_ids:
        state["sort_modes"].pop(f"userfolder:{fid}", None)

    # Release docs that were placed inside removed folders unless re-placed in the new tree
    for doc_uuid in list(state["doc_placements"].keys()):
        if doc_uuid in seen_docs:
            continue
        p_id = state["doc_placements"][doc_uuid].get("parentId")
        if p_id in removed_ids:
            state["doc_placements"].pop(doc_uuid, None)

    for doc_uuid, parent_uf_id, doc_so in doc_assignments:
        state["doc_placements"][doc_uuid] = {"parentId": parent_uf_id, "sortOrder": doc_so}

    if set_manual_sort:
        if raw_tree:
            state["sort_modes"][root_sort_key] = "manual"
        for fkey in folders_with_kids:
            state["sort_modes"][fkey] = "manual"

    if root_parent_id and str(root_parent_id).startswith(("book:", "paper:")):
        target_doc_uuid = str(root_parent_id).split(":", 1)[1]
        if takeover and target_doc_uuid not in state["custom_tree_docs"]:
            state["custom_tree_docs"].append(target_doc_uuid)
        elif "takeover" in payload and payload["takeover"] is False:
            state["custom_tree_docs"] = [d for d in state["custom_tree_docs"] if d != target_doc_uuid]

    before_sections: dict[str, str] = {}
    if sync_sections and section_mapping:
        try:
            _, before_sections = library.update_published_sections(section_mapping)
        except ValueError as exc:
            raise FolderError(str(exc), 404) from None

    extra = {"before_sections": before_sections} if before_sections else None
    summary_text = (
        f"应用目录树（{root_sort_key}：新建 {stats['folders_created']}、更新 {stats['folders_updated']}、"
        f"移除 {stats['folders_removed']}、归档 {stats['questions_assigned']} 题）"
    )
    op = _record_history(state, "apply_tree", summary_text, before, extra=extra)
    return ({"summary": stats, "root": root_sort_key, "mode": mode, "takeover": takeover, "message": summary_text}, op)


def mutate(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise FolderError("请求格式必须是 JSON 对象")
    action = str(payload.get("action") or "").strip()
    if not action:
        raise FolderError("请指定操作类型 action")

    with _LOCK:
        state = _load_unlocked()
        before = _snapshot(state)
        now_iso = timezone.now().isoformat()
        message = ""
        op_record: dict | None = None
        extra_response: dict = {}

        if action == "create":
            name = _clean_name(payload.get("name"))
            if len(state["user_folders"]) >= MAX_FOLDERS:
                raise FolderError(f"自建文件夹最多支持 {MAX_FOLDERS} 个")
            parent_id = _normalize_parent_id(payload.get("parent_id"), state["user_folders"])
            doc_types = _paper_type_map()
            if (
                parent_id
                and _parent_depth(
                    parent_id, state["user_folders"], doc_placements=state["doc_placements"], doc_types=doc_types
                )
                + 1
                > MAX_FOLDER_DEPTH
            ):
                raise FolderError(f"文件夹层级最多支持 {MAX_FOLDER_DEPTH} 层，无法继续新建子文件夹")
            qids = _resolve_publication_ids(payload["question_ids"]) if payload.get("question_ids") else []
            created = {
                "id": _gen_id("uf"),
                "name": name,
                "parentId": parent_id,
                "questionIds": qids,
                "createdAt": int(time.time() * 1000),
            }
            sort_order = _clean_sort_order(payload.get("sort_order"))
            if sort_order is not None:
                created["sortOrder"] = sort_order
            state["user_folders"].append(created)
            op_record = _record_history(state, "create", f"新建文件夹「{name}」", before)
            message = f"已新建文件夹「{name}」"

        elif action == "rename":
            target = _find_user_folder(state["user_folders"], payload.get("folder_id") or "")
            new_name = _clean_name(payload.get("name"))
            old_name = target["name"]
            if new_name == old_name:
                return overview() | {"message": "文件夹名称未变化", "changed": False}
            target["name"] = new_name
            op_record = _record_history(state, "rename", f"重命名文件夹「{old_name}」→「{new_name}」", before)
            message = f"已重命名为「{new_name}」"

        elif action == "move":
            raw_folder = str(payload.get("folder_id") or "").strip()
            if payload.get("doc_id") or raw_folder.startswith(("paper:", "book:")):
                doc_types = _paper_type_map()
                message, op_record, changed = _mutate_move_doc(state, payload, doc_types, before)
                if not changed:
                    return overview() | {"message": message, "changed": False}
            else:
                target = _find_user_folder(state["user_folders"], raw_folder)
                new_parent = _normalize_parent_id(payload.get("parent_id"), state["user_folders"])
                has_sort = "sort_order" in payload
                new_sort = _clean_sort_order(payload.get("sort_order")) if has_sort else None
                if (target.get("parentId") or None) == new_parent and (
                    not has_sort or target.get("sortOrder") == new_sort
                ):
                    return overview() | {"message": "文件夹已在目标层级中", "changed": False}
                doc_types = _paper_type_map()
                if new_parent:
                    if _is_descendant_or_self(
                        state["user_folders"],
                        target["id"],
                        new_parent,
                        doc_placements=state["doc_placements"],
                        doc_types=doc_types,
                    ):
                        raise FolderError("不能将文件夹移动到自身或其子文件夹下")
                parent_depth = _parent_depth(
                    new_parent, state["user_folders"], doc_placements=state["doc_placements"], doc_types=doc_types
                )
                height = _subtree_height(
                    state["user_folders"], target["id"], doc_placements=state["doc_placements"], doc_types=doc_types
                )
                if parent_depth + height > MAX_FOLDER_DEPTH:
                    raise FolderError(f"移动后文件夹总层级将超过 {MAX_FOLDER_DEPTH} 层上限")
                target["parentId"] = new_parent
                if has_sort:
                    if new_sort is None:
                        target.pop("sortOrder", None)
                    else:
                        target["sortOrder"] = new_sort
                op_record = _record_history(state, "move", f"移动文件夹「{target['name']}」", before)
                message = f"已移动文件夹「{target['name']}」"

        elif action == "move_doc":
            doc_types = _paper_type_map()
            message, op_record, changed = _mutate_move_doc(state, payload, doc_types, before)
            if not changed:
                return overview() | {"message": message, "changed": False}

        elif action == "delete":
            target = _find_user_folder(state["user_folders"], payload.get("folder_id") or "")
            subtree = _collect_subtree_ids(state["user_folders"], target["id"])
            state["user_folders"] = [item for item in state["user_folders"] if item["id"] not in subtree]
            for fid in subtree:
                state["sort_modes"].pop(f"userfolder:{fid}", None)
            # Release any docs placed inside the deleted subtree back to default group (never delete docs/questions)
            for doc_uuid in list(state["doc_placements"].keys()):
                pid = state["doc_placements"][doc_uuid].get("parentId")
                if pid in subtree:
                    state["doc_placements"].pop(doc_uuid, None)
            op_record = _record_history(
                state, "delete", f"删除文件夹「{target['name']}」（含 {len(subtree)} 个目录）", before
            )
            message = f"已删除文件夹「{target['name']}」"

        elif action == "reorder":
            doc_types = _paper_type_map()
            message, op_record = _mutate_reorder(state, payload, doc_types, before)

        elif action == "set_custom_tree":
            raw_doc = payload.get("doc_id") or payload.get("paper_id") or payload.get("target_id") or ""
            paper = _resolve_doc_paper(str(raw_doc))
            doc_uuid = str(paper.pk)
            enabled = bool(payload.get("enabled", True))
            currently_enabled = doc_uuid in state["custom_tree_docs"]
            if enabled == currently_enabled:
                return overview() | {
                    "message": f"「{paper.display_name}」已处于{'自定义目录' if enabled else '自动章节目录'}模式",
                    "changed": False,
                }
            if enabled:
                state["custom_tree_docs"].append(doc_uuid)
            else:
                state["custom_tree_docs"] = [d for d in state["custom_tree_docs"] if d != doc_uuid]
            op_record = _record_history(
                state,
                "set_custom_tree",
                f"{'启用' if enabled else '恢复自动'}「{paper.display_name}」自定义目录",
                before,
            )
            message = f"已为「{paper.display_name}」{'启用自定义目录树' if enabled else '恢复自动章节目录'}"

        elif action == "apply_tree":
            doc_types = _paper_type_map()
            apply_res, op_record = _mutate_apply_tree(state, payload, doc_types, before)
            if apply_res.get("dry_run"):
                return apply_res
            message = apply_res.pop("message", "已应用目录树")
            extra_response = apply_res

        elif action == "add_questions":
            raw_target = str(payload.get("target_id") or payload.get("folder_id") or "").strip()
            if not raw_target:
                raise FolderError("请指定目标文件夹 target_id")
            qids = _resolve_publication_ids(payload.get("question_ids"))
            if raw_target.startswith(("paper:", "book:")):
                doc_id = raw_target.split(":", 1)[1]
                try:
                    doc_uuid = str(uuid.UUID(doc_id))
                except ValueError:
                    raise FolderError("资料编号格式不正确") from None
                if not Paper.objects.filter(pk=doc_uuid).exists():
                    raise FolderError(f"找不到资料：{doc_id}", 404)
                ov = state["doc_overrides"].setdefault(doc_uuid, {"added": [], "removed": []})
                pubs_in_doc = {
                    str(pk)
                    for pk in PublishedQuestion.objects.filter(
                        paper_id=doc_uuid, status=PublishedQuestion.Status.PUBLISHED
                    ).values_list("pk", flat=True)
                }
                added_set = set(ov["added"])
                removed_set = set(ov["removed"])
                doc_added_count = 0
                for qid in qids:
                    was_in = qid in added_set or (qid in pubs_in_doc and qid not in removed_set)
                    if qid in removed_set:
                        removed_set.discard(qid)
                    if qid not in pubs_in_doc:
                        added_set.add(qid)
                    if not was_in:
                        doc_added_count += 1
                sections_map = dict(ov.get("sections") or {})
                if not added_set and not removed_set and not sections_map:
                    state["doc_overrides"].pop(doc_uuid, None)
                else:
                    add_entry: dict[str, object] = {"added": sorted(added_set), "removed": sorted(removed_set)}
                    if sections_map:
                        add_entry["sections"] = sections_map
                    state["doc_overrides"][doc_uuid] = add_entry
                op_record = _record_history(state, "add_questions", f"将 {len(qids)} 道题归入资料文件夹", before)
                message = f"已将 {len(qids)} 道题放入资料文件夹（新增 {doc_added_count} 题）"
            else:
                folder = _find_user_folder(state["user_folders"], raw_target)
                existing = set(folder.get("questionIds") or [])
                added_count = 0
                for qid in qids:
                    if qid not in existing:
                        existing.add(qid)
                        folder["questionIds"].append(qid)
                        added_count += 1
                op_record = _record_history(
                    state, "add_questions", f"将 {len(qids)} 道题归入「{folder['name']}」", before
                )
                message = f"已将 {len(qids)} 道题放入「{folder['name']}」（新增 {added_count} 题）"

        elif action == "remove_questions":
            raw_target = str(payload.get("target_id") or payload.get("folder_id") or "").strip()
            if not raw_target:
                raise FolderError("请指定目标文件夹 target_id")
            remove_qids = set(_resolve_publication_ids(payload.get("question_ids")))
            if raw_target.startswith(("paper:", "book:")):
                doc_id = raw_target.split(":", 1)[1]
                try:
                    doc_uuid = str(uuid.UUID(doc_id))
                except ValueError:
                    raise FolderError("资料编号格式不正确") from None
                ov = state["doc_overrides"].get(doc_uuid) or {"added": [], "removed": []}
                pubs_in_doc = {
                    str(pk)
                    for pk in PublishedQuestion.objects.filter(
                        paper_id=doc_uuid, status=PublishedQuestion.Status.PUBLISHED
                    ).values_list("pk", flat=True)
                }
                added_set = set(ov["added"]) - remove_qids
                removed_set = set(ov["removed"]) | (remove_qids & pubs_in_doc)
                sections_map = {k: v for k, v in (ov.get("sections") or {}).items() if k not in remove_qids}
                if not added_set and not removed_set and not sections_map:
                    state["doc_overrides"].pop(doc_uuid, None)
                else:
                    remove_entry: dict[str, object] = {"added": sorted(added_set), "removed": sorted(removed_set)}
                    if sections_map:
                        remove_entry["sections"] = sections_map
                    state["doc_overrides"][doc_uuid] = remove_entry
                op_record = _record_history(
                    state, "remove_questions", f"从资料文件夹移出 {len(remove_qids)} 道题", before
                )
                message = f"已从资料文件夹移出 {len(remove_qids)} 道题"
            else:
                folder = _find_user_folder(state["user_folders"], raw_target)
                subtree = _collect_subtree_ids(state["user_folders"], folder["id"])
                removed_count = 0
                for item in state["user_folders"]:
                    if item["id"] in subtree:
                        prev_len = len(item.get("questionIds") or [])
                        item["questionIds"] = [q for q in (item.get("questionIds") or []) if q not in remove_qids]
                        removed_count += prev_len - len(item["questionIds"])
                op_record = _record_history(
                    state, "remove_questions", f"从「{folder['name']}」移出 {removed_count} 条题目归档", before
                )
                message = f"已从「{folder['name']}」移出 {removed_count} 条归档"

        elif action == "set_sort":
            target_id = str(payload.get("target_id") or "").strip()
            mode = str(payload.get("mode") or "").strip()
            if mode not in (*VALID_SORT_MODES, "default"):
                raise FolderError("排序方式只能是 time_desc、time_asc、name、count_desc、manual 或 default")
            if not target_id or target_id in {"global", "all"}:
                state["paper_sort_mode"] = DEFAULT_SORT_MODE if mode == "default" else mode
                label = "全局默认"
            else:
                # Allow bare user folder id -> userfolder:<id>
                if any(item["id"] == target_id for item in state["user_folders"]):
                    target_id = f"userfolder:{target_id}"
                if mode == "default":
                    state["sort_modes"].pop(target_id, None)
                else:
                    state["sort_modes"][target_id] = mode
                label = target_id
            op_record = _record_history(state, "set_sort", f"设置 {label} 排序为 {mode}", before)
            message = f"已设置 {label} 排序方式"

        elif action == "create_smart":
            name = _clean_name(payload.get("name"), "智能文件夹名称")
            if len(state["smart_folders"]) >= MAX_SMART_FOLDERS:
                raise FolderError(f"智能文件夹最多支持 {MAX_SMART_FOLDERS} 个")
            raw_parent = payload.get("parent_id")
            parent_id = None
            if raw_parent and str(raw_parent).strip() not in {"__root__", "section:custom"}:
                parent_id = _find_smart_folder(state["smart_folders"], str(raw_parent))["id"]
                if _parent_depth(parent_id, state["smart_folders"], is_custom=True) + 1 > MAX_FOLDER_DEPTH:
                    raise FolderError(f"智能文件夹层级最多支持 {MAX_FOLDER_DEPTH} 层")
            flt = payload.get("filter") if isinstance(payload.get("filter"), dict) else {}
            created_smart = {
                "id": _gen_id("csf"),
                "name": name,
                "parentId": parent_id,
                "filter": deepcopy(flt),
                "createdAt": int(time.time() * 1000),
            }
            state["smart_folders"].append(created_smart)
            op_record = _record_history(state, "create_smart", f"新建智能文件夹「{name}」", before)
            message = f"已新建智能文件夹「{name}」"

        elif action == "rename_smart":
            target = _find_smart_folder(state["smart_folders"], payload.get("folder_id") or "")
            new_name = _clean_name(payload.get("name"), "智能文件夹名称")
            old_name = target["name"]
            target["name"] = new_name
            op_record = _record_history(state, "rename_smart", f"重命名智能文件夹「{old_name}」→「{new_name}」", before)
            message = f"已重命名为「{new_name}」"

        elif action == "move_smart":
            target = _find_smart_folder(state["smart_folders"], payload.get("folder_id") or "")
            raw_parent = payload.get("parent_id")
            new_parent = None
            if raw_parent and str(raw_parent).strip() not in {"__root__", "section:custom"}:
                new_parent = _find_smart_folder(state["smart_folders"], str(raw_parent))["id"]
                if _is_descendant_or_self(state["smart_folders"], target["id"], new_parent, is_custom=True):
                    raise FolderError("不能将智能文件夹移动到自身或其子文件夹下")
                parent_depth = _parent_depth(new_parent, state["smart_folders"], is_custom=True)
                height = _subtree_height(state["smart_folders"], target["id"], is_custom=True)
                if parent_depth + height > MAX_FOLDER_DEPTH:
                    raise FolderError(f"移动后智能文件夹总层级将超过 {MAX_FOLDER_DEPTH} 层上限")
            target["parentId"] = new_parent
            op_record = _record_history(state, "move_smart", f"移动智能文件夹「{target['name']}」", before)
            message = f"已移动智能文件夹「{target['name']}」"

        elif action == "delete_smart":
            target = _find_smart_folder(state["smart_folders"], payload.get("folder_id") or "")
            subtree = _collect_subtree_ids(state["smart_folders"], target["id"])
            state["smart_folders"] = [item for item in state["smart_folders"] if item["id"] not in subtree]
            for cid in subtree:
                state["sort_modes"].pop(f"custom:{cid}", None)
            op_record = _record_history(state, "delete_smart", f"删除智能文件夹「{target['name']}」", before)
            message = f"已删除智能文件夹「{target['name']}」"

        elif action == "set_section":
            mapping: dict[str, str] = {}
            items = payload.get("items")
            if isinstance(items, list) and items:
                if len(items) > MAX_BATCH_QUESTIONS:
                    raise FolderError(f"一次最多整理 {MAX_BATCH_QUESTIONS} 道题的章节目录")
                resolved_pairs = []
                for entry in items:
                    if not isinstance(entry, dict) or not entry.get("publication_id"):
                        raise FolderError("items 每项须包含 publication_id 和 section")
                    pub_id = _resolve_publication_ids([entry["publication_id"]])[0]
                    sec = str(entry.get("section") or "").strip()[:120]
                    if any(ord(ch) < 32 for ch in sec):
                        raise FolderError("章节名称不能包含控制字符")
                    resolved_pairs.append((pub_id, sec))
                for pub_id, sec in resolved_pairs:
                    mapping[pub_id] = sec
            else:
                if "section" not in payload or not isinstance(payload.get("section"), str):
                    raise FolderError("请提供章节目录 section（或传 items 映射列表）")
                sec = payload["section"].strip()[:120]
                if any(ord(ch) < 32 for ch in sec):
                    raise FolderError("章节名称不能包含控制字符")
                qids = _resolve_publication_ids(payload.get("question_ids"))
                for pid in qids:
                    mapping[pid] = sec
            try:
                changed_count, before_sections = library.update_published_sections(mapping)
            except ValueError as exc:
                raise FolderError(str(exc), 404) from None
            op_record = _record_history(
                state,
                "set_section",
                f"整理 {len(mapping)} 道题的章节目录（实际变更 {changed_count} 道）",
                before,
                extra={"before_sections": before_sections},
            )
            message = f"已整理 {len(mapping)} 道题的章节目录（更新 {changed_count} 道）"

        elif action == "sync":
            # Frontend Eagle UI sync
            client_rev = payload.get("revision")
            if isinstance(client_rev, int) and state["revision"] > 0 and client_rev < state["revision"]:
                # Server already has newer agent changes; do not overwrite with stale client cache.
                return overview() | {"synced": False, "stale": True, "message": "服务端已有更新的文件夹改动"}
            if "user_folders" in payload:
                state["user_folders"] = _clean_user_folders(payload.get("user_folders"))
            if "doc_overrides" in payload:
                state["doc_overrides"] = _clean_doc_overrides(payload.get("doc_overrides"))
            if "doc_placements" in payload:
                state["doc_placements"] = _clean_doc_placements(payload.get("doc_placements"))
            if "custom_tree_docs" in payload:
                state["custom_tree_docs"] = _clean_custom_tree_docs(payload.get("custom_tree_docs"))
            if "smart_folders" in payload:
                state["smart_folders"] = _clean_smart_folders(payload.get("smart_folders"))
            if "sort_modes" in payload:
                state["sort_modes"] = _clean_sort_modes(payload.get("sort_modes"))
            if payload.get("paper_sort_mode") in VALID_SORT_MODES:
                state["paper_sort_mode"] = payload["paper_sort_mode"]
            message = "已同步前端文件夹状态"

        elif action == "undo":
            return undo(payload.get("operation_id"))

        else:
            raise FolderError(f"不支持的文件夹操作：{action}")

        state["revision"] += 1
        state["updated_at"] = now_iso
        _save_unlocked(state)

    result = overview()
    result.update(extra_response)
    result["message"] = message
    if op_record is not None:
        result["operation_id"] = op_record["id"]
    return result


def undo(operation_id: str | None = None) -> dict:
    with _LOCK:
        state = _load_unlocked()
        history = state["history"]
        target = None
        if operation_id:
            for item in reversed(history):
                if item.get("id") == str(operation_id):
                    target = item
                    break
            if target is None:
                raise FolderError("找不到要撤销的文件夹操作记录", 404)
            if target.get("undone_at"):
                return overview() | {"ok": True, "replayed": True, "message": "该操作已经撤销过了"}
        else:
            for item in reversed(history):
                if not item.get("undone_at"):
                    target = item
                    break
            if target is None:
                raise FolderError("没有可撤销的文件夹操作", 409)

        before = target.get("before")
        if isinstance(before, dict) and before and target["action"] != "set_section":
            state["user_folders"] = _clean_user_folders(before.get("user_folders"))
            state["doc_overrides"] = _clean_doc_overrides(before.get("doc_overrides"))
            state["doc_placements"] = _clean_doc_placements(before.get("doc_placements"))
            state["custom_tree_docs"] = _clean_custom_tree_docs(before.get("custom_tree_docs"))
            state["smart_folders"] = _clean_smart_folders(before.get("smart_folders"))
            state["sort_modes"] = _clean_sort_modes(before.get("sort_modes"))
            psort = before.get("paper_sort_mode")
            state["paper_sort_mode"] = psort if psort in VALID_SORT_MODES else DEFAULT_SORT_MODE

        before_sections = target.get("before_sections") or {}
        if before_sections:
            try:
                library.update_published_sections(before_sections)
            except ValueError as exc:
                raise FolderError(f"部分题目已下架或不存在，无法完全撤销章节改动：{exc}", 409) from None

        target["undone_at"] = timezone.now().isoformat()
        state["revision"] += 1
        state["updated_at"] = target["undone_at"]
        _save_unlocked(state)

    result = overview()
    result["undone_operation_id"] = target["id"]
    result["message"] = f"已撤销：{target.get('summary') or target['action']}"
    return result


@csrf_exempt
def folders_view(request):
    from .views import _body, _error, _guard

    if request.method == "GET":
        include_tree = request.GET.get("tree", "1") != "0"
        return JsonResponse(overview(include_tree=include_tree))
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request, limit=2_000_000)
    if payload is None:
        return _error("请求内容必须是合法的 JSON 对象")
    try:
        return JsonResponse(mutate(payload))
    except FolderError as error:
        return _error(str(error), error.status)


@csrf_exempt
def undo_view(request):
    from .views import _body, _error, _guard

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request) or {}
    try:
        return JsonResponse(undo(payload.get("operation_id")))
    except FolderError as error:
        return _error(str(error), error.status)


CLOUD_FOLDER_NAME = "来自云端"


def assign_publications_to_cloud_folder(pub_ids: list[str], *, folder_name: str = CLOUD_FOLDER_NAME) -> dict:
    """将云端下载/导入的题目默认归入顶级自定义文件夹「来自云端」（幂等创建或复用）。"""
    clean_ids = _resolve_publication_ids(pub_ids)
    target_name = str(folder_name or CLOUD_FOLDER_NAME).strip() or CLOUD_FOLDER_NAME
    with _LOCK:
        state = _load_unlocked()
        user_folders = state["user_folders"]
        target = next(
            (
                item
                for item in user_folders
                if item.get("parentId") is None and str(item.get("name") or "").strip() == target_name
            ),
            None,
        )
        created = False
        if target is None:
            existing_ids = {str(item.get("id") or "") for item in user_folders}
            folder_id = _gen_id("uf")
            while folder_id in existing_ids:
                folder_id = _gen_id("uf")
            orders = [
                int(item["sortOrder"])
                for item in user_folders
                if item.get("parentId") is None and item.get("sortOrder") is not None
            ]
            target = {
                "id": folder_id,
                "name": target_name,
                "parentId": None,
                "questionIds": [],
                "createdAt": int(time.time() * 1000),
                "sortOrder": (max(orders) + 1) if orders else len(user_folders),
            }
            user_folders.append(target)
            created = True

        existing_qids = [str(qid) for qid in (target.get("questionIds") or [])]
        seen = set(existing_qids)
        added_count = 0
        for pid in clean_ids:
            if pid not in seen:
                seen.add(pid)
                existing_qids.append(pid)
                added_count += 1
        target["questionIds"] = existing_qids

        if created or added_count > 0:
            state["revision"] = int(state.get("revision") or 0) + 1
            state["updated_at"] = timezone.now().isoformat()
            _save_unlocked(state)

        return {
            "folder_id": str(target.get("id") or ""),
            "folder_name": target_name,
            "created": created,
            "added_count": added_count,
            "total_count": len(existing_qids),
            "revision": int(state.get("revision") or 0),
        }

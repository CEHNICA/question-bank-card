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

MAX_FOLDER_DEPTH = 4
MAX_FOLDERS = 500
MAX_SMART_FOLDERS = 200
MAX_HISTORY = 50
MAX_NAME_LEN = 120
MAX_BATCH_QUESTIONS = 500
VALID_SORT_MODES = ("time_desc", "time_asc", "name", "count_desc")
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
        out.append(
            {"id": fid, "name": name, "parentId": parent_id or None, "questionIds": qids, "createdAt": int(created_at)}
        )
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
        out.append(
            {
                "id": fid,
                "name": name,
                "parentId": parent_id or None,
                "filter": deepcopy(flt),
                "createdAt": int(created_at),
            }
        )
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
    raise FolderError(f"找不到父级文件夹：{raw_parent}", 404)


def _parent_depth(parent_id: str | None, items: list[dict], *, is_custom: bool = False) -> int:
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
        if raw_id.startswith(("paper:", "book:")):
            return 2
        if raw_id.startswith("bookchap:"):
            return 3
        if raw_id.startswith("booksec:"):
            return 4
    by_id = {item["id"]: item for item in items}
    depth = 0
    cursor: str | None = raw_id
    visited: set[str] = set()
    while cursor and cursor not in visited:
        visited.add(cursor)
        if not is_custom:
            if cursor in {"group:papers", "group:books"}:
                return depth + 1
            if cursor.startswith(("paper:", "book:")):
                return depth + 2
            if cursor.startswith("bookchap:"):
                return depth + 3
            if cursor.startswith("booksec:"):
                return depth + 4
        item = by_id.get(cursor)
        if not item:
            break
        depth += 1
        cursor = item.get("parentId")
    return depth


def _subtree_height(items: list[dict], folder_id: str) -> int:
    by_parent: dict[str | None, list[str]] = {}
    for item in items:
        by_parent.setdefault(item.get("parentId") or None, []).append(item["id"])
    visited: set[str] = set()

    def walk(fid: str) -> int:
        if fid in visited:
            return 0
        visited.add(fid)
        kids = by_parent.get(fid, [])
        return 1 + max((walk(kid) for kid in kids), default=0)

    return max(1, walk(folder_id))


def _is_descendant_or_self(items: list[dict], source_id: str, target_parent_id: str | None) -> bool:
    if not target_parent_id:
        return False
    if source_id == target_parent_id:
        return True
    by_id = {item["id"]: item for item in items}
    cursor: str | None = target_parent_id
    visited: set[str] = set()
    while cursor and cursor not in visited:
        if cursor == source_id:
            return True
        visited.add(cursor)
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

    def uf_subtree_qids(fid: str) -> set[str]:
        ids = _collect_subtree_ids(user_folders, fid)
        qset: set[str] = set()
        for item in user_folders:
            if item["id"] in ids:
                qset.update(item.get("questionIds") or [])
        return qset

    by_parent: dict[str, list[dict]] = {}
    known_uf_ids = {item["id"] for item in user_folders}
    for item in user_folders:
        pid = item.get("parentId")
        key = pid if (pid and (pid in known_uf_ids or _is_system_parent(pid))) else "__root__"
        by_parent.setdefault(key, []).append(item)

    def build_uf_node(item: dict) -> dict:
        node_id = f"userfolder:{item['id']}"
        kids = [build_uf_node(child) for child in by_parent.get(item["id"], [])]
        return {
            "id": item["id"],
            "node_id": node_id,
            "name": item["name"],
            "parent_id": item.get("parentId"),
            "direct_count": len(item.get("questionIds") or []),
            "count": len(uf_subtree_qids(item["id"])),
            "question_ids": list(item.get("questionIds") or []),
            "sort_mode": sort_modes.get(node_id, ""),
            "children": kids,
        }

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

    papers_out = []
    books_out = []
    for paper in Paper.objects.order_by("-created_at"):
        pid = str(paper.pk)
        pub_list = pubs_by_paper.get(pid, [])
        ov = doc_overrides.get(pid) or {"added": [], "removed": []}
        published_count = max(0, len(pub_list) + len(ov["added"]) - len(ov["removed"]))
        prefix = "book:" if paper.material_type == Paper.MaterialType.BOOK else "paper:"
        node_id = f"{prefix}{pid}"
        subfolders = [build_uf_node(child) for child in by_parent.get(node_id, [])]
        entry = {
            "id": pid,
            "node_id": node_id,
            "name": paper.display_name,
            "filename": paper.filename,
            "material_type": paper.material_type,
            "status": paper.status,
            "published": len(pub_list),
            "folder_count": published_count,
            "sort_mode": sort_modes.get(node_id, ""),
            "subfolders": subfolders,
        }
        if paper.material_type == Paper.MaterialType.BOOK:
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
                        "children": [{"name": sname or "（未标注栏目）", "count": cnt} for sname, cnt in subs.items()],
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
            "children": [build_smart_node(child) for child in smart_by_parent.get(item["id"], [])],
        }

    from . import library_tag_groups

    return {
        "global_sort_mode": global_sort,
        "sort_modes": dict(sort_modes),
        "user_folders": [build_uf_node(item) for item in by_parent.get("__root__", [])],
        "papers_group_subfolders": [build_uf_node(item) for item in by_parent.get("group:papers", [])],
        "books_group_subfolders": [build_uf_node(item) for item in by_parent.get("group:books", [])],
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

        if action == "create":
            name = _clean_name(payload.get("name"))
            if len(state["user_folders"]) >= MAX_FOLDERS:
                raise FolderError(f"自建文件夹最多支持 {MAX_FOLDERS} 个")
            parent_id = _normalize_parent_id(payload.get("parent_id"), state["user_folders"])
            if parent_id and _parent_depth(parent_id, state["user_folders"], is_custom=False) + 1 > MAX_FOLDER_DEPTH:
                raise FolderError(f"文件夹层级最多支持 {MAX_FOLDER_DEPTH} 层，无法继续新建子文件夹")
            qids = _resolve_publication_ids(payload["question_ids"]) if payload.get("question_ids") else []
            created = {
                "id": _gen_id("uf"),
                "name": name,
                "parentId": parent_id,
                "questionIds": qids,
                "createdAt": int(time.time() * 1000),
            }
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
            target = _find_user_folder(state["user_folders"], payload.get("folder_id") or "")
            new_parent = _normalize_parent_id(payload.get("parent_id"), state["user_folders"])
            if (target.get("parentId") or None) == new_parent:
                return overview() | {"message": "文件夹已在目标层级中", "changed": False}
            if new_parent and not _is_system_parent(new_parent):
                if _is_descendant_or_self(state["user_folders"], target["id"], new_parent):
                    raise FolderError("不能将文件夹移动到自身或其子文件夹下")
            parent_depth = _parent_depth(new_parent, state["user_folders"], is_custom=False)
            height = _subtree_height(state["user_folders"], target["id"])
            if parent_depth + height > MAX_FOLDER_DEPTH:
                raise FolderError(f"移动后文件夹总层级将超过 {MAX_FOLDER_DEPTH} 层上限")
            target["parentId"] = new_parent
            op_record = _record_history(state, "move", f"移动文件夹「{target['name']}」", before)
            message = f"已移动文件夹「{target['name']}」"

        elif action == "delete":
            target = _find_user_folder(state["user_folders"], payload.get("folder_id") or "")
            subtree = _collect_subtree_ids(state["user_folders"], target["id"])
            state["user_folders"] = [item for item in state["user_folders"] if item["id"] not in subtree]
            for fid in subtree:
                state["sort_modes"].pop(f"userfolder:{fid}", None)
            op_record = _record_history(
                state, "delete", f"删除文件夹「{target['name']}」（含 {len(subtree)} 个目录）", before
            )
            message = f"已删除文件夹「{target['name']}」"

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
                changed = 0
                for qid in qids:
                    was_in = qid in added_set or (qid in pubs_in_doc and qid not in removed_set)
                    if qid in removed_set:
                        removed_set.discard(qid)
                    if qid not in pubs_in_doc:
                        added_set.add(qid)
                    if not was_in:
                        changed += 1
                sections_map = dict(ov.get("sections") or {})
                if not added_set and not removed_set and not sections_map:
                    state["doc_overrides"].pop(doc_uuid, None)
                else:
                    add_entry: dict[str, object] = {"added": sorted(added_set), "removed": sorted(removed_set)}
                    if sections_map:
                        add_entry["sections"] = sections_map
                    state["doc_overrides"][doc_uuid] = add_entry
                op_record = _record_history(state, "add_questions", f"将 {len(qids)} 道题归入资料文件夹", before)
                message = f"已将 {len(qids)} 道题放入资料文件夹（新增 {changed} 题）"
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
                raise FolderError("排序方式只能是 time_desc、time_asc、name、count_desc 或 default")
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
                if _is_descendant_or_self(state["smart_folders"], target["id"], new_parent):
                    raise FolderError("不能将智能文件夹移动到自身或其子文件夹下")
                parent_depth = _parent_depth(new_parent, state["smart_folders"], is_custom=True)
                height = _subtree_height(state["smart_folders"], target["id"])
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

        if target["action"] == "set_section":
            before_sections = target.get("before_sections") or {}
            if before_sections:
                try:
                    library.update_published_sections(before_sections)
                except ValueError as exc:
                    raise FolderError(f"部分题目已下架或不存在，无法完全撤销章节改动：{exc}", 409) from None
        else:
            before = target.get("before") or {}
            state["user_folders"] = _clean_user_folders(before.get("user_folders"))
            state["doc_overrides"] = _clean_doc_overrides(before.get("doc_overrides"))
            state["smart_folders"] = _clean_smart_folders(before.get("smart_folders"))
            state["sort_modes"] = _clean_sort_modes(before.get("sort_modes"))
            psort = before.get("paper_sort_mode")
            state["paper_sort_mode"] = psort if psort in VALID_SORT_MODES else DEFAULT_SORT_MODE

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

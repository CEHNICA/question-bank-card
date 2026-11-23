"""Personal tag groups (LibraryTagGroup) for organizing tags without altering knowledge hierarchy.

Kept on ``/api/library/tag-groups`` so ``/api/library/tags`` stays strictly focused on the
formal tag hierarchy while still allowing users and AI assistants to organize personal groups
with full undo support via ``LibraryTagOperation``.
"""

from __future__ import annotations

import re
import uuid

from django.db import transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import LibraryTag, LibraryTagGroup, LibraryTagOperation

MAX_GROUP_NAME = 100
MAX_BATCH_TAGS = 500


class TagGroupError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _clean_name(value) -> str:
    if not isinstance(value, str):
        raise TagGroupError("分组名称必须是文字")
    name = " ".join(value.split()).strip()
    if not name or len(name) > MAX_GROUP_NAME or "|" in name or any(ord(ch) < 32 for ch in name):
        raise TagGroupError(f"分组名称需为 1–{MAX_GROUP_NAME} 个字符")
    return name


def _clean_color(value) -> str:
    color = str(value or "").strip()
    if color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise TagGroupError("颜色格式应为 #RRGGBB")
    return color


def _group_snapshot(group: LibraryTagGroup) -> dict:
    tags = list(group.tags.order_by("name", "id"))
    return {
        "id": str(group.pk),
        "name": group.name,
        "color": group.color,
        "position": group.position,
        "system_key": group.system_key,
        "tag_ids": [str(tag.pk) for tag in tags],
        "tags": [{"id": str(tag.pk), "name": tag.name, "kind": tag.kind, "color": tag.color} for tag in tags],
    }


def _find_group(ref: str, *, lock: bool = False) -> LibraryTagGroup:
    text = str(ref or "").strip()
    if not text:
        raise TagGroupError("请指定分组编号或名称")
    qs = LibraryTagGroup.objects.select_for_update() if lock else LibraryTagGroup.objects
    try:
        gid = uuid.UUID(text)
    except ValueError:
        gid = None
    if gid is not None:
        group = qs.filter(pk=gid).first()
        if group is not None:
            return group
    group = qs.filter(name=text).first()
    if group is None:
        raise TagGroupError(f"找不到个人分组：{text}", 404)
    return group


def _resolve_tags(refs) -> list[LibraryTag]:
    if not isinstance(refs, list) or len(refs) > MAX_BATCH_TAGS:
        raise TagGroupError(f"标签列表最多包含 {MAX_BATCH_TAGS} 个标签")
    resolved: list[LibraryTag] = []
    seen: set[str] = set()
    for raw in refs:
        text = str(raw or "").strip()
        if not text:
            continue
        tag = None
        try:
            tid = uuid.UUID(text)
            tag = LibraryTag.objects.filter(pk=tid).first()
        except ValueError:
            matches = list(LibraryTag.objects.filter(name=text, active=True))
            if len(matches) == 1:
                tag = matches[0]
            elif len(matches) > 1:
                raise TagGroupError(f"标签名「{text}」存在多个类型，请使用标签 UUID") from None
        if tag is None:
            raise TagGroupError(f"找不到标签：{text}", 404)
        key = str(tag.pk)
        if key not in seen:
            seen.add(key)
            resolved.append(tag)
    return resolved


def list_groups_data() -> list[dict]:
    groups = LibraryTagGroup.objects.prefetch_related("tags").order_by("position", "name", "id")
    return [_group_snapshot(group) for group in groups]


def _operations_data() -> list[dict]:
    rows = LibraryTagOperation.objects.filter(action__startswith="tag_group:").order_by("-created_at", "-id")[:20]
    return [
        {
            "id": str(row.pk),
            "action": row.action.removeprefix("tag_group:"),
            "summary": row.after_snapshot.get("summary", ""),
            "created_at": row.created_at.isoformat(),
            "undone": bool(row.undone_at),
        }
        for row in rows
    ]


def list_overview() -> dict:
    return {"ok": True, "groups": list_groups_data(), "operations": _operations_data()}


def mutate(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise TagGroupError("请求格式必须是 JSON 对象")
    action = str(payload.get("action") or "").strip()
    if action == "undo":
        return undo(payload.get("operation_id"))

    with transaction.atomic():
        op_id = uuid.uuid4()
        before: dict = {}
        after: dict = {}
        summary = ""

        if action == "create_group":
            name = _clean_name(payload.get("name"))
            if LibraryTagGroup.objects.filter(name=name).exists():
                raise TagGroupError(f"已经存在同名个人分组「{name}」", 409)
            color = _clean_color(payload.get("color"))
            position = payload.get("position")
            if position is None:
                last_pos = LibraryTagGroup.objects.order_by("-position").values_list("position", flat=True).first()
                position = (last_pos + 1) if last_pos is not None else 0
            elif isinstance(position, bool) or not isinstance(position, int) or position < 0:
                raise TagGroupError("排序位置 position 必须是非负整数")
            tags = _resolve_tags(payload.get("tag_ids") or [])
            group = LibraryTagGroup.objects.create(name=name, color=color, position=position)
            if tags:
                group.tags.set(tags)
            after = {"group": _group_snapshot(group)}
            summary = f"新建个人分组「{name}」"

        elif action == "update_group":
            group = _find_group(payload.get("group_id") or payload.get("group") or "", lock=True)
            before = {"group": _group_snapshot(group)}
            if "name" in payload and payload["name"] is not None:
                name = _clean_name(payload["name"])
                if name != group.name and LibraryTagGroup.objects.filter(name=name).exists():
                    raise TagGroupError(f"已经存在同名个人分组「{name}」", 409)
                group.name = name
            if "color" in payload and payload["color"] is not None:
                group.color = _clean_color(payload["color"])
            if "position" in payload and payload["position"] is not None:
                pos = payload["position"]
                if isinstance(pos, bool) or not isinstance(pos, int) or pos < 0:
                    raise TagGroupError("排序位置 position 必须是非负整数")
                group.position = pos
            group.save()
            after = {"group": _group_snapshot(group)}
            summary = f"更新个人分组「{group.name}」"

        elif action == "delete_group":
            group = _find_group(payload.get("group_id") or payload.get("group") or "", lock=True)
            before = {"group": _group_snapshot(group)}
            name = group.name
            group.delete()
            after = {"deleted_id": before["group"]["id"]}
            summary = f"删除个人分组「{name}」"

        elif action in {"add_tags", "remove_tags", "set_tags"}:
            group = _find_group(payload.get("group_id") or payload.get("group") or "", lock=True)
            tags = _resolve_tags(payload.get("tag_ids") or [])
            before = {"group": _group_snapshot(group)}
            if action == "add_tags":
                if tags:
                    group.tags.add(*tags)
                summary = f"向个人分组「{group.name}」添加 {len(tags)} 个标签"
            elif action == "remove_tags":
                if tags:
                    group.tags.remove(*tags)
                summary = f"从个人分组「{group.name}」移出 {len(tags)} 个标签"
            else:
                group.tags.set(tags)
                summary = f"设置个人分组「{group.name}」标签（共 {len(tags)} 个）"
            after = {"group": _group_snapshot(group)}

        else:
            raise TagGroupError(f"不支持的个人分组操作：{action}")

        after["summary"] = summary
        LibraryTagOperation.objects.create(
            client_request_id=op_id,
            request_hash=op_id.hex,
            action=f"tag_group:{action}",
            before_snapshot=before,
            after_snapshot=after,
        )
    return list_overview() | {"operation_id": str(op_id), "message": summary}


def undo(operation_id: str | None = None) -> dict:
    with transaction.atomic():
        qs = LibraryTagOperation.objects.select_for_update().filter(action__startswith="tag_group:")
        if operation_id:
            try:
                req_id = uuid.UUID(str(operation_id))
            except ValueError:
                raise TagGroupError("撤销记录编号格式不正确") from None
            op = qs.filter(client_request_id=req_id).first()
            if op is None:
                op = qs.filter(pk=req_id).first()
            if op is None:
                raise TagGroupError("找不到对应的个人分组操作记录", 404)
            if op.undone_at:
                return list_overview() | {"replayed": True, "message": "该操作已经撤销过了"}
        else:
            op = qs.filter(undone_at__isnull=True).order_by("-created_at", "-id").first()
            if op is None:
                raise TagGroupError("没有可撤销的个人分组操作", 409)

        action = op.action.removeprefix("tag_group:")
        before = op.before_snapshot or {}
        after = op.after_snapshot or {}

        if action == "create_group":
            gid = (after.get("group") or {}).get("id")
            group = LibraryTagGroup.objects.filter(pk=gid).first()
            if group is not None:
                group.delete()
        elif action == "delete_group":
            snap = before.get("group") or {}
            if LibraryTagGroup.objects.filter(name=snap.get("name")).exists():
                raise TagGroupError(f"已有同名个人分组「{snap.get('name')}」，无法撤销删除", 409)
            group = LibraryTagGroup.objects.create(
                id=snap["id"],
                name=snap["name"],
                color=snap.get("color", ""),
                position=int(snap.get("position") or 0),
                system_key=snap.get("system_key"),
            )
            valid_tag_ids = list(
                LibraryTag.objects.filter(pk__in=snap.get("tag_ids") or []).values_list("pk", flat=True)
            )
            group.tags.set(valid_tag_ids)
        elif action in {"update_group", "add_tags", "remove_tags", "set_tags"}:
            snap = before.get("group") or {}
            group = LibraryTagGroup.objects.filter(pk=snap.get("id")).first()
            if group is None:
                raise TagGroupError("原个人分组已不存在，无法撤销", 409)
            group.name = snap["name"]
            group.color = snap.get("color", "")
            group.position = int(snap.get("position") or 0)
            group.save()
            valid_tag_ids = list(
                LibraryTag.objects.filter(pk__in=snap.get("tag_ids") or []).values_list("pk", flat=True)
            )
            group.tags.set(valid_tag_ids)
        else:
            raise TagGroupError("此类个人分组操作不支持撤销", 409)

        op.undone_at = timezone.now()
        op.save(update_fields=["undone_at"])
        summary = after.get("summary") or action
    return list_overview() | {"message": f"已撤销：{summary}"}


@csrf_exempt
def tag_groups_view(request):
    from .views import _body, _error, _guard

    if request.method == "GET":
        return JsonResponse(list_overview())
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容必须是合法的 JSON 对象")
    try:
        return JsonResponse(mutate(payload))
    except TagGroupError as error:
        return _error(str(error), error.status)

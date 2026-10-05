"""给题库里的题手工改知识点标签：只动标签，不新建版本、不重新审核。

标签打错了以前只有一条出路——撤回重录。标签是附加分类，不是题面的一部分，
所以这里复用 ``library.save_extras``：题面快照、版本号、审核状态一概不动。

人工选的词同样必须在知识点目录里。目录的设计前提是「不让模型自己造词」，
手滑造出来的词永远筛不准，所以校验一视同仁。
"""

from __future__ import annotations

import copy

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import knowledge, library
from .models import PublishedQuestion

_TAG_KEYS = ("tags", "tags_source", "tags_at", "tags_fingerprint", "tags_publication_id",
             "tags_agent", "tags_executor", "tags_checked")


class TagError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _catalogue() -> list[str]:
    return [item["point"] for item in knowledge.load()]


def clean(raw) -> list[str]:
    """Validate against the catalogue, dedupe in order, cap at ``knowledge.MAX_TAGS``."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TagError("标签要是一个列表")
    allowed = set(_catalogue())
    chosen: list[str] = []
    for item in raw:
        value = str(item if not isinstance(item, str) else item).replace("|", " ").strip()[:60]
        if not value:
            continue
        if value not in allowed:
            raise TagError(f"“{value}”不在知识点目录里；请从目录里挑，或先在设置里改目录")
        if value in chosen:
            continue
        chosen.append(value)
    if len(chosen) > knowledge.MAX_TAGS:
        raise TagError(f"一道题最多 {knowledge.MAX_TAGS} 个知识点")
    return chosen


def apply(publication: PublishedQuestion, tags: list[str]) -> PublishedQuestion:
    extras = copy.deepcopy(publication.extras) if isinstance(publication.extras, dict) else {}
    for key in _TAG_KEYS:
        extras.pop(key, None)
    if tags:
        extras["tags"] = tags
        # tags_source/tags_executor 平时记的是「哪个引擎打的」，人工改完就换成 human。
        extras["tags_source"] = "human"
        extras["tags_executor"] = "human"
        extras["tags_at"] = timezone.now().isoformat()
        # Bind by task, not by URL: this version's images get new file names
        # when the question is republished, but the tags still describe the same task.
        extras["tags_fingerprint"] = library.generation_fingerprint(publication.content or {}, publication.id)
        extras["tags_publication_id"] = str(publication.id)
    return library.save_extras(publication, extras)


def data(publication: PublishedQuestion) -> dict:
    extras = publication.extras if isinstance(publication.extras, dict) else {}
    return {
        "publication": str(publication.id),
        "tags": library.tags_of(extras),
        "catalogue": knowledge.load(),
        "max": knowledge.MAX_TAGS,
        "source": str(extras.get("tags_source") or ""),
        "at": str(extras.get("tags_at") or ""),
    }


@csrf_exempt
def tags_view(request, publication_id):
    if request.method not in {"GET", "POST"}:
        return JsonResponse({"error": "不支持的请求方式"}, status=405)
    from .views import _body, _guard
    if request.method == "POST":
        rejected = _guard(request)
        if rejected is not None:
            return rejected
    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    try:
        if request.method == "GET":
            return JsonResponse(data(publication))
        # A body we cannot read must not read as "clear every tag".
        payload = _body(request)
        if payload is None:
            return JsonResponse({"error": "请求内容无法读取，标签没有改动"}, status=400)
        publication = apply(publication, clean(payload.get("tags")))
        return JsonResponse({"ok": True, "tags": library.tags_of(publication.extras),
                             "publication": library.publication_json(publication)})
    except TagError as error:
        return JsonResponse({"error": str(error)}, status=error.status)

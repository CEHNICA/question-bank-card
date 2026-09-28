"""正式题库：把终审通过的题卡保存成不可变快照（与 M3 题库页面的数据格式一致）。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import shutil
import unicodedata
import uuid
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import imaging
from .figure_policy import (
    CONFIRMED_NO_FIGURE, blocking_message, blocks_approval, stored_or_derived_review,
)
from .models import Paper, PublishedQuestion, Question, QuestionGroup

CHOICE_TYPES = {"single_choice", "multiple_choice"}
OPTION_KEYS = ("A", "B", "C", "D")
REVIEWABLE_STATES = {Question.State.GREEN, Question.State.YELLOW}


def figure_file(question: Question, index: int) -> Path:
    """按题卡保存的配图坐标从高清原页裁图（缓存，坐标变了文件名就变）。"""
    from .pipeline import PageStore, paper_dir

    figure = question.figures[index]
    digest = hashlib.sha1(json.dumps([figure["page_idx"], figure["bbox"]]).encode()).hexdigest()[:12]
    target = paper_dir(question.paper) / "figures" / f"q{question.id}_{digest}.png"
    if not target.is_file():
        page = PageStore(question.paper).load(figure["page_idx"])
        target.parent.mkdir(parents=True, exist_ok=True)
        page.crop(imaging.to_pixels(figure["bbox"], page.size)).save(target, format="PNG", optimize=True)
    return target


def final_content(question: Question) -> dict:
    is_choice = question.question_type in CHOICE_TYPES or bool(question.options)
    source_origin = "manual" if question.start_source == "manual" or question.regions != question.regions_auto \
        else question.start_source
    return {
        "number": question.number,
        "section": question.section,
        "question_type": question.question_type,
        "stem": question.stem,
        "options": {k: (question.options or {}).get(k, "") for k in OPTION_KEYS} if is_choice else {},
        "answer": question.answer or "",
        "analysis": question.analysis or "",
        "figures": [
            {"slot": f["slot"], "page_idx": f["page_idx"], "bbox": f["bbox"],
             "source": f.get("source", "unknown")}
            for f in question.figures
        ],
        "sources": [
            {"page_idx": r["page_idx"], "bbox": r["bbox"], "type": "text", "source": source_origin}
            for r in question.regions
        ],
        "document_id": str(question.paper_id),
        "source_filename": question.paper.display_name,
        "source_group": ({
            "id": question.group_id,
            "title": question.group.title,
            "sequence": question.group.sequence,
        } if question.group_id else None),
        # 审核记录随不可变快照保存。一般提示不参与版本身份；人工“确实无图”的决定例外，
        # 因为它是允许一条原本会被阻止的题目入库的关键依据。
        "review": {
            "state": question.state,
            "flags": list(question.flags or []),
            "approved_at": question.approved_at.isoformat() if question.approved_at else None,
            "approved_content_hash": question.approved_content_hash,
            "text_source": question.text_source,
            "edited": question.edited,
            "figure_review": deepcopy(stored_or_derived_review(question)),
        },
    }


def content_hash(content: dict) -> str:
    """题目内容与追溯来源的稳定哈希；忽略图片 URL 等发布时生成的字段。"""
    material = {k: content.get(k) for k in (
        "number", "section", "question_type", "stem", "options", "answer", "analysis",
        "document_id", "source_filename",
    )}
    material["figures"] = [
        {k: f.get(k) for k in ("slot", "page_idx", "bbox", "source")}
        for f in content.get("figures", [])
    ]
    material["sources"] = [
        {k: source.get(k) for k in ("page_idx", "bbox", "type", "source")}
        for source in content.get("sources", [])
    ]
    figure_review = (content.get("review") or {}).get("figure_review") or {}
    if (figure_review.get("status") == CONFIRMED_NO_FIGURE
            and figure_review.get("source") == "human"):
        material["figure_review_decision"] = {
            "status": CONFIRMED_NO_FIGURE,
            "source": "human",
            "confirmed_at": figure_review.get("confirmed_at"),
        }
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def approval_hash(question: Question) -> str:
    """当前草稿需要由人确认的精确内容版本。"""
    return content_hash(final_content(question))


def approval_is_current(question: Question) -> bool:
    if blocks_approval(stored_or_derived_review(question)):
        return False
    return bool(
        question.approved
        and question.approved_content_hash
        and question.state in REVIEWABLE_STATES
        and question.stem.strip()
        and question.approved_content_hash == approval_hash(question)
    )


_LATEX_SYMBOLS = {
    "angle": "∠", "parallel": "∥", "perp": "⊥", "bot": "⊥", "triangle": "△", "times": "×",
    "div": "÷", "leqslant": "≤", "geqslant": "≥", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "ne": "≠", "pi": "π", "circ": "°", "sqrt": "√", "cdot": "·", "in": "∈",
    "cup": "∪", "cap": "∩", "infty": "∞", "mid": "|", "pm": "±", "because": "∵", "therefore": "∴",
    "alpha": "α", "beta": "β", "gamma": "γ", "theta": "θ", "square": "□", "cong": "≅", "sim": "∽",
    "forall": "∀", "exists": "∃", "emptyset": "∅", "varnothing": "∅", "subset": "⊂", "subseteq": "⊆",
}
_LATEX_COMMAND = re.compile(r"\\([A-Za-z]+)|\\([{}|,;!% ])")


def search_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))

    def replace(match: re.Match) -> str:
        if match.group(1):
            return _LATEX_SYMBOLS.get(match.group(1), "")
        return match.group(2) if match.group(2) in "{}|" else ""

    text = _LATEX_COMMAND.sub(replace, text)
    text = text.replace("//", "∥").replace("▱", "□").replace("丄", "⊥")
    text = re.sub(r"[\s$^_{}]", "", text)
    return text.lower()


def _search_text(content: dict) -> str:
    parts = [content["stem"], *content["options"].values(), content["answer"], content["analysis"],
             content["source_filename"], (content.get("source_group") or {}).get("title", "")]
    return search_key(" ".join(parts))


def publish(question: Question) -> tuple[PublishedQuestion, bool]:
    """入库一题。内容没变就不重复生成版本。返回 (快照, 是否新建)。"""
    with transaction.atomic():
        try:
            paper = Paper.objects.select_for_update().get(pk=question.paper_id)
        except Paper.DoesNotExist:
            raise ValueError("这份试卷任务已删除，不能再入库") from None
        try:
            question = Question.objects.select_for_update().select_related("paper").get(pk=question.pk)
        except Question.DoesNotExist:
            raise ValueError("这道题已放入回收站，请恢复后再入库") from None
        if not question.approved:
            raise ValueError(f"第 {question.number} 题还没有通过终审")
        if question.state not in REVIEWABLE_STATES:
            raise ValueError(f"第 {question.number} 题当前状态不能入库，请先完成识读或人工修正")
        if not question.stem.strip():
            raise ValueError(f"第 {question.number} 题题干为空")
        figure_review = stored_or_derived_review(question)
        if blocks_approval(figure_review):
            raise ValueError(f"第 {question.number} 题暂时不能入库：{blocking_message(figure_review)}")
        content = final_content(question)
        digest = content_hash(content)
        if not question.approved_content_hash or question.approved_content_hash != digest:
            raise ValueError(f"第 {question.number} 题通过后内容已变化，请重新终审")
        latest = question.publications.order_by("-version").first()
        if latest and latest.status == PublishedQuestion.Status.PUBLISHED and latest.content_hash == digest:
            return latest, False
        publication_id = uuid.uuid4()
        folder = settings.DATA_ROOT / "library" / str(publication_id)
        folder.mkdir(parents=True, exist_ok=False)
        try:
            for index, figure in enumerate(content["figures"]):
                name = f"figure-{index + 1}.png"
                shutil.copyfile(figure_file(question, index), folder / name)
                figure["file"] = name
                figure["url"] = f"/api/library/{publication_id}/figures/{name}"
            publication = PublishedQuestion.objects.create(
                id=publication_id, question=question, paper=paper,
                source_filename=paper.display_name, number=question.number,
                question_type=question.question_type, version=(latest.version if latest else 0) + 1,
                content=content, content_hash=digest, search_text=_search_text(content),
            )
            question.publications.filter(status=PublishedQuestion.Status.PUBLISHED).exclude(
                pk=publication.pk).update(status=PublishedQuestion.Status.SUPERSEDED)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    return publication, True


def rename_paper(paper: Paper, name: str) -> tuple[Paper, bool]:
    """修改任务显示名，并同步所有草稿审批与正式题来源标签。

    filename 始终保留原上传文件名。任务名属于来源元数据；重命名不会新建题目版本，
    也不会把原本已经失效的人工审批重新变成有效。
    """
    with transaction.atomic():
        paper = Paper.objects.select_for_update().get(pk=paper.pk)
        old_name = paper.display_name
        if name == old_name:
            return paper, False

        questions = list(
            Question.all_objects.select_for_update().select_related("paper", "group").filter(paper=paper)
        )
        groups = list(QuestionGroup.objects.select_for_update().filter(paper=paper))
        current_approval_ids = [question.pk for question in questions if approval_is_current(question)]
        publications = list(PublishedQuestion.objects.select_for_update().filter(paper=paper))

        # 输入恢复为原文件名时不保存一份重复值。
        paper.task_name = "" if name == paper.filename else name
        paper.save(update_fields=["task_name", "updated_at"])

        renamed_groups = {}
        for group in groups:
            if group.title == old_name:
                group.title = name
            elif group.title.startswith(f"{old_name}（"):
                group.title = f"{name}{group.title[len(old_name):]}"[:255]
            else:
                continue
            group.updated_at = timezone.now()
            renamed_groups[group.pk] = group.title
        if renamed_groups:
            QuestionGroup.objects.bulk_update(
                [group for group in groups if group.pk in renamed_groups], ["title", "updated_at"],
            )

        if current_approval_ids:
            approved_questions = list(
                Question.all_objects.select_related("paper", "group").filter(pk__in=current_approval_ids)
            )
            for question in approved_questions:
                question.approved_content_hash = approval_hash(question)
            Question.all_objects.bulk_update(approved_questions, ["approved_content_hash"])

        for publication in publications:
            old_hash = publication.content_hash
            content = deepcopy(publication.content)
            content["source_filename"] = paper.display_name
            source_group = content.get("source_group")
            if isinstance(source_group, dict) and source_group.get("id") in renamed_groups:
                source_group["title"] = renamed_groups[source_group["id"]]
            new_hash = content_hash(content)
            review = content.get("review")
            if isinstance(review, dict) and review.get("approved_content_hash") == old_hash:
                review["approved_content_hash"] = new_hash
            publication.source_filename = paper.display_name
            publication.content = content
            publication.content_hash = new_hash
            publication.search_text = _search_text(content)
        if publications:
            PublishedQuestion.objects.bulk_update(
                publications, ["source_filename", "content", "content_hash", "search_text"]
            )
    return paper, True


def publication_state(question: Question) -> dict | None:
    live = question.publications.filter(status=PublishedQuestion.Status.PUBLISHED).order_by("-version").first()
    if live is None:
        return None
    return {"id": str(live.id), "version": live.version,
            "up_to_date": live.content_hash == content_hash(final_content(question))}


def withdraw(publication: PublishedQuestion) -> PublishedQuestion:
    if publication.status == PublishedQuestion.Status.PUBLISHED:
        publication.status = PublishedQuestion.Status.WITHDRAWN
        publication.withdrawn_at = timezone.now()
        publication.save(update_fields=["status", "withdrawn_at"])
    return publication


def publication_json(publication: PublishedQuestion) -> dict:
    return {
        "id": str(publication.id),
        "draft_id": publication.question_id,
        "document_id": str(publication.paper_id) if publication.paper_id else None,
        "source_filename": publication.source_filename,
        "number": publication.number,
        "question_type": publication.question_type,
        "version": publication.version,
        "status": publication.status,
        "published_at": publication.published_at.isoformat(),
        "withdrawn_at": publication.withdrawn_at.isoformat() if publication.withdrawn_at else None,
        "content": publication.content,
    }

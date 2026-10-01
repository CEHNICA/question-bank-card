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

from . import features, imaging, prose, qtypes
from .figure_policy import (
    DECISION_FLAGS,
    CONFIRMED_NO_FIGURE, blocking_message, blocks_approval, stored_or_derived_review,
)
from .models import LibraryJob, Paper, PublishedQuestion, Question, QuestionGroup
from .textnorm import strip_example_label, strip_type_label

CHOICE_TYPES = set(qtypes.CHOICE_TYPES)
OPTION_KEYS = ("A", "B", "C", "D", "E")
# Every choice question carries A–D in its content, empty or not; E only when
# printed.  A four-option question therefore keeps the exact content (and
# approval hash) it had before E existed.
BASE_OPTION_KEYS = ("A", "B", "C", "D")


def option_content(options: dict | None) -> dict:
    options = options or {}
    content = {key: options.get(key, "") for key in BASE_OPTION_KEYS}
    if str(options.get("E") or "").strip():
        content["E"] = options["E"]
    return content
REVIEWABLE_STATES = {Question.State.GREEN, Question.State.YELLOW}


# A figure may continue on the next page (a table cut by a page break): the
# extra pieces are kept in ``parts`` and joined below the first one.
MAX_FIGURE_PARTS = 4


def figure_parts(figure: dict) -> list[dict]:
    parts = figure.get("parts") if isinstance(figure, dict) else None
    if not isinstance(parts, list):
        return []
    return [
        {"page_idx": part["page_idx"], "bbox": part["bbox"]}
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("page_idx"), int)
        and isinstance(part.get("bbox"), list) and len(part["bbox"]) == 4
    ]


def figure_identity(figure: dict) -> str:
    """What the cropped image depends on.  A one-piece figure keeps its old key."""
    parts = figure_parts(figure)
    if not parts:
        return json.dumps([figure["page_idx"], figure["bbox"]])
    return json.dumps([[figure["page_idx"], figure["bbox"]]] + [[p["page_idx"], p["bbox"]] for p in parts])


def figure_file(question: Question, index: int) -> Path:
    """按题卡保存的配图坐标从高清原页裁图（缓存，坐标变了文件名就变）。"""
    from .pipeline import PageStore, paper_dir

    figure = question.figures[index]
    digest = hashlib.sha1(figure_identity(figure).encode()).hexdigest()[:12]
    target = paper_dir(question.paper) / "figures" / f"q{question.id}_{digest}.png"
    if not target.is_file():
        store = PageStore(question.paper)
        target.parent.mkdir(parents=True, exist_ok=True)
        parts = figure_parts(figure)
        if parts:
            pieces = []
            for piece in [{"page_idx": figure["page_idx"], "bbox": figure["bbox"]}, *parts]:
                page = store.load(piece["page_idx"])
                pieces.append((page, imaging.to_pixels(piece["bbox"], page.size)))
            imaging.stack_figure_pieces(pieces).save(target, format="PNG", optimize=True)
        else:
            page = store.load(figure["page_idx"])
            page.crop(imaging.to_pixels(figure["bbox"], page.size)).save(target, format="PNG", optimize=True)
    return target


def _content_figure(figure: dict) -> dict:
    item = {"slot": figure["slot"], "page_idx": figure["page_idx"], "bbox": figure["bbox"],
            "source": figure.get("source", "unknown")}
    parts = figure_parts(figure)
    if parts:
        item["parts"] = parts
    return item


def final_content(question: Question) -> dict:
    is_choice = question.question_type in CHOICE_TYPES or bool(question.options)
    source_origin = "manual" if question.start_source == "manual" or question.regions != question.regions_auto \
        else question.start_source
    return {
        "number": question.number,
        "section": question.section,
        "question_type": question.question_type,
        "stem": question.stem,
        "options": option_content(question.options) if is_choice else {},
        "answer": question.answer or "",
        "analysis": question.analysis or "",
        "origin": question.origin or "",
        "figures": [_content_figure(f) for f in question.figures],
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
        # Only a stitched figure carries parts, so every other hash is unchanged.
        | ({"parts": figure_parts(f)} if figure_parts(f) else {})
        for f in content.get("figures", [])
    ]
    material["sources"] = [
        {k: source.get(k) for k in ("page_idx", "bbox", "type", "source")}
        for source in content.get("sources", [])
    ]
    # 题源为空时不进校验：升级前通过、入库的题，校验值和以前一模一样。
    if str(content.get("origin") or "").strip():
        material["origin"] = content["origin"]
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


# 谁打的勾。人对照原卷确认是 human；AI 助手（tiyouju 命令行、MCP）打的勾是 ai。
APPROVAL_SOURCES = ("human", "ai")
DEFAULT_AGENT = "AI 助手"


def agent_name(value) -> str:
    """A short, printable name for the AI assistant that approved a card."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(character if character.isprintable() else " " for character in text)
    return " ".join(text.split())[:40] or DEFAULT_AGENT


def approval_source(question: Question) -> str:
    """human / ai for an approved card ("" when not approved); old approvals are human."""
    if not question.approved:
        return ""
    return question.approval_source if question.approval_source in APPROVAL_SOURCES else "human"


def approve(question: Question, *, now, source: str = "human", agent: str = "") -> bool:
    """Approve the version on screen.  An AI tick never replaces a person's.

    Returns whether anything changed.  The caller has already checked that the
    card can be approved and saves it.
    """
    if source not in APPROVAL_SOURCES:
        raise ValueError("approval source must be human or ai")
    if source == "ai" and approval_is_current(question) and approval_source(question) == "human":
        return False
    question.approved = True
    question.approved_at = now
    question.approved_content_hash = approval_hash(question)
    question.approval_source = source
    question.approval_agent = agent_name(agent) if source == "ai" else ""
    return True


def confirm_published_review(question: Question) -> int:
    """A person confirmed a card an AI had passed and published: the library
    copy of that same version now counts as human-reviewed (no new version)."""
    if approval_source(question) != "human" or not question.approved_content_hash:
        return 0
    return question.publications.filter(
        status=PublishedQuestion.Status.PUBLISHED, review_source="ai",
        content_hash=content_hash(final_content(question)),
    ).update(review_source="human", review_agent="")


def type_blocks_approval(question: Question) -> bool:
    """题型还没定的题不能通过、不能入库（题库里不再出现“题型待核对”）。"""
    return not qtypes.decided(question.question_type)


def approval_is_current(question: Question) -> bool:
    if blocks_approval(stored_or_derived_review(question)) or type_blocks_approval(question):
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


def _search_text(content: dict, extras: dict | None = None) -> str:
    parts = [content["stem"], *content["options"].values(), content["answer"], content["analysis"],
             content["source_filename"], (content.get("source_group") or {}).get("title", ""),
             str(content.get("origin") or ""), *tags_of(extras)]
    return search_key(" ".join(parts))


def tags_of(extras: dict | None) -> list[str]:
    tags = (extras or {}).get("tags")
    return [str(tag) for tag in tags if str(tag).strip()] if isinstance(tags, list) else []


def tags_text(tags) -> str:
    """“|甲|乙|”：一个标签一段，筛选时整段匹配，“函数”不会命中“函数的应用”。"""
    cleaned = [str(tag).replace("|", " ").strip() for tag in (tags or []) if str(tag).strip()]
    return f"|{'|'.join(cleaned)}|" if cleaned else ""


def carried_extras(previous: PublishedQuestion | None, content: dict) -> dict:
    """What a new version inherits: tags always; an AI answer only for the same task text."""
    if previous is None or not isinstance(previous.extras, dict):
        return {}
    extras = {}
    if tags_of(previous.extras):
        extras["tags"] = tags_of(previous.extras)
        if previous.extras.get("tags_source"):
            extras["tags_source"] = previous.extras["tags_source"]
    answer = previous.extras.get("ai_answer")
    old = previous.content or {}
    if isinstance(answer, dict) and old.get("stem") == content.get("stem") \
            and (old.get("options") or {}) == (content.get("options") or {}):
        extras["ai_answer"] = answer
    return extras


def save_extras(publication: PublishedQuestion, extras: dict) -> PublishedQuestion:
    """Store tags / AI answer on a library entry: no new version, no re-approval."""
    publication.extras = extras
    publication.tags_text = tags_text(tags_of(extras))
    publication.search_text = _search_text(publication.content, extras)
    publication.save(update_fields=["extras", "tags_text", "search_text"])
    return publication


def already_published(question: Question) -> bool:
    """The card's newest library version is live and is exactly what was approved:
    ``publish`` would change nothing.  Uses ``question.versions`` (all versions,
    newest first) when the caller prefetched them.  A version an AI passed that a
    person has since approved is left to ``publish``, which relabels it."""
    versions = getattr(question, "versions", None)
    if versions is None:
        latest = question.publications.order_by("-version").first()
    else:
        latest = versions[0] if versions else None
    if latest is None or latest.status != PublishedQuestion.Status.PUBLISHED:
        return False
    if latest.review_source == "ai" and approval_source(question) == "human":
        return False
    return bool(question.approved_content_hash) and latest.content_hash == question.approved_content_hash \
        and approval_is_current(question)


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
        if type_blocks_approval(question):
            raise ValueError(f"第 {question.number} 题{qtypes.BLOCK_MESSAGE}，再入库")
        content = final_content(question)
        digest = content_hash(content)
        if not question.approved_content_hash or question.approved_content_hash != digest:
            raise ValueError(f"第 {question.number} 题通过后内容已变化，请重新终审")
        source = approval_source(question) or "human"
        agent = question.approval_agent if source == "ai" else ""
        latest = question.publications.order_by("-version").first()
        if latest and latest.status == PublishedQuestion.Status.PUBLISHED and latest.content_hash == digest:
            # A person confirmed what an AI assistant had passed: same version, now human-reviewed.
            if latest.review_source == "ai" and source == "human":
                latest.review_source, latest.review_agent = "human", ""
                latest.save(update_fields=["review_source", "review_agent"])
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
            extras = carried_extras(latest, content)
            publication = PublishedQuestion.objects.create(
                id=publication_id, question=question, paper=paper,
                source_filename=paper.display_name, number=question.number,
                question_type=question.question_type, version=(latest.version if latest else 0) + 1,
                content=content, content_hash=digest, search_text=_search_text(content, extras),
                review_source=source, review_agent=agent,
                extras=extras, tags_text=tags_text(tags_of(extras)),
            )
            question.publications.filter(status=PublishedQuestion.Status.PUBLISHED).exclude(
                pk=publication.pk).update(status=PublishedQuestion.Status.SUPERSEDED)
            # 还在排队的知识点、AI 答案跟着到新的一版上做。
            LibraryJob.objects.filter(
                publication__question=question,
                status__in=(LibraryJob.Status.QUEUED, LibraryJob.Status.RUNNING),
            ).exclude(publication=publication).update(publication=publication)
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
            publication.search_text = _search_text(content, publication.extras)
        if publications:
            PublishedQuestion.objects.bulk_update(
                publications, ["source_filename", "content", "content_hash", "search_text"]
            )
    return paper, True


LIVE_PUBLICATION_FIELDS = ("id", "question_id", "version", "content_hash", "status")


def live_publications_prefetch():
    """For a list of cards: each card's published versions in one query (``publication_state`` uses it)."""
    from django.db.models import Prefetch

    return Prefetch(
        "publications",
        queryset=PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED)
        .only(*LIVE_PUBLICATION_FIELDS).order_by("-version"),
        to_attr="live_publications",
    )


def publication_state(question: Question) -> dict | None:
    prefetched = getattr(question, "live_publications", None)
    if prefetched is not None:
        live = prefetched[0] if prefetched else None
    else:
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
        "review": {"source": publication.review_source or "human", "agent": publication.review_agent},
        "published_at": publication.published_at.isoformat(),
        "withdrawn_at": publication.withdrawn_at.isoformat() if publication.withdrawn_at else None,
        "content": publication.content,
        "origin": str((publication.content or {}).get("origin") or ""),
        "has_answer": bool(str((publication.content or {}).get("answer") or "").strip()),
        "subquestions": qtypes.subquestion_count((publication.content or {}).get("stem")),
        "tags": tags_of(publication.extras),
        "ai_answer": (publication.extras or {}).get("ai_answer") if isinstance(publication.extras, dict) else None,
    }


_E_TAG = re.compile(r"\s*【\s*E\s*】\s*")
_LABEL_SETS_TYPE = {"unknown", "", "single_choice", "multiple_choice"}


def tidy_text(stem: str, options: dict | None, question_type: str, origin: str = "", *,
              switches: dict | None = None) -> tuple[str, dict, str, str]:
    """Apply the saved-card fixes that only reformat, never re-read.

    * a leading “例1” label is dropped (1.5.1);
    * a leading “（多项选择题）” note is dropped and names the type, unless a
      person already made it a fill-in or free-response question;
    * “3个【E】4个” in option D — an E the parser did not know about — is
      split into D “3个” and E “4个”;
    * a printed source note in front (“[2026××中学月考]”) moves to the origin,
      and "…" in Chinese text becomes “…” (1.10, both switchable).

    Returns (stem, options, type, origin).  A person's origin is kept.
    """
    switches = switches if switches is not None else features.load()
    new_stem = strip_example_label(stem or "")
    new_stem, labelled = strip_type_label(new_stem)
    if new_stem != (stem or ""):
        new_stem = new_stem.lstrip()
    new_type = question_type or "unknown"
    if labelled and new_type in _LABEL_SETS_TYPE:
        new_type = labelled
    new_stem, new_origin, labelled = prose.tidy_stem(new_stem, origin=origin or "", switches=switches)
    if labelled and new_type in _LABEL_SETS_TYPE:
        new_type = labelled
    new_options = dict(options or {})
    fourth = new_options.get("D")
    if isinstance(fourth, str) and _E_TAG.search(fourth) and not str(new_options.get("E") or "").strip():
        before, after = _E_TAG.split(fourth, maxsplit=1)
        if before.strip() and after.strip():
            new_options["D"] = before.strip()
            new_options["E"] = after.strip()
    new_options = {key: prose.tidy_value(value, switches=switches) if isinstance(value, str) else value
                   for key, value in new_options.items()}
    return new_stem, new_options, new_type, new_origin


def _tidy_reading(reading, switches: dict | None = None, origin: str = ""):
    if not isinstance(reading, dict):
        return reading
    stem, options, _kind, _origin = tidy_text(
        reading.get("stem") if isinstance(reading.get("stem"), str) else "",
        reading.get("options") if isinstance(reading.get("options"), dict) else {},
        "unknown", origin, switches=switches,
    )
    changed = dict(reading)
    if isinstance(reading.get("stem"), str):
        changed["stem"] = stem
    if isinstance(reading.get("options"), dict):
        changed["options"] = options
    return changed


_BRACKET_NOTE = re.compile(r"[（(【\[][^）)】\]]{0,14}[）)】\]]")


def _without_invented_options(options: dict, figure_slots: set[str] = frozenset()) -> tuple[dict, list[str]]:
    """Options without a model's “（原卷此处为配图…）” note, and the letters that were only that.

    An option that has its picture (a picture option) just loses the note.
    """
    from .readers import strip_bracketed_figure_descriptions

    kept, dropped = {}, []
    for key, value in (options or {}).items():
        text, described = strip_bracketed_figure_descriptions(str(value or ""))
        if described and not text.strip():
            if key not in figure_slots:
                dropped.append(key)
            continue
        kept[key] = text if described else value
    return kept, sorted(dropped)


def _restore_from_readings(options: dict, letters: list[str], question: Question) -> tuple[dict, list[str]]:
    """Take a dropped option from a reading that did read it (高一质量检测一第 11 题：读法甲读出了
    “f(1,5)=f(5,1)”，裁决却写成了配图说明)."""
    from .readers import strip_bracketed_figure_descriptions
    from .textnorm import canon

    restored: list[str] = []
    kept = dict(options)
    existing = {canon(str(value)) for value in kept.values() if str(value or "").strip()}
    for letter in letters:
        for reading in (question.read_a, question.read_b):
            if not isinstance(reading, dict) or not isinstance(reading.get("options"), dict):
                continue
            text, described = strip_bracketed_figure_descriptions(str(reading["options"].get(letter) or ""))
            # “（图片）”, “（图略）”: another note, not the option.
            note = _BRACKET_NOTE.fullmatch(text.strip()) and "图" in text
            if described or note or not text.strip() or canon(text) in existing:
                continue
            kept[letter] = text.strip()
            existing.add(canon(text))
            restored.append(letter)
            break
    return dict(sorted(kept.items())), restored


def tidy_saved_cards() -> dict[str, int]:
    """Bring cards read by older versions up to the current text rules.

    Only formatting moves (see tidy_text); every word, figure and range stays.
    A card whose approval was current keeps it: the reviewer approved that
    task, and a label or a misplaced “【E】” is not part of it.  Published
    snapshots get the same treatment in place, as a task rename updates
    their file name, so publishing again does not mint a new version.  Safe
    to run on every start: once everything is tidy it changes nothing.

    1.10: a readable card whose type is still undecided gets the “题型没读出来”
    reminder (yellow); it cannot be approved until a type is chosen.
    """

    from .pipeline import read_c_with_spots

    switches = features.load()
    counts = {"questions": 0, "publications": 0}
    with transaction.atomic():
        for question in Question.all_objects.select_for_update().select_related("paper", "group"):
            stem, options, kind, origin = tidy_text(
                question.stem, question.options, question.question_type, question.origin, switches=switches)
            if not (question.approved or question.edited or question.type_locked):
                # 1.10.1: a choice card under “…有多项符合题目要求…” is multiple
                # choice, whatever the reader said.  Only cards nobody has
                # approved, edited or typed by hand.
                kind = qtypes.with_section(kind, question.section)
            invented: list[str] = []
            if not (question.approved or question.edited):
                # 1.10.2: an option the model wrote as “（原卷此处为配图，无印刷文字）”
                # was not read; say so instead of keeping the note as its text.
                figure_slots = {figure.get("slot") for figure in question.figures or [] if isinstance(figure, dict)}
                options, invented = _without_invented_options(options, figure_slots)
            answer = prose.tidy_value(question.answer, switches=switches)
            analysis = prose.tidy_value(question.analysis, switches=switches)
            current_flags = list(question.flags or [])
            if invented:
                options, restored = _restore_from_readings(options, invented, question)
                current_flags.extend(f"选项 {letter} 只有一次识读读到，已补上，请对照原卷核对" for letter in restored)
                missing = [letter for letter in invented if letter not in restored]
                if missing:
                    current_flags.append(f"选项 {'、'.join(missing)} 没有读出来，请对照原卷补上")
            base_state = question.state
            review = question.figure_review if isinstance(question.figure_review, dict) else {}
            if review.get("source") == "human" and any(flag in DECISION_FLAGS for flag in current_flags):
                # 1.10.2: a person already settled the figures (e.g. confirmed 无图), yet
                # “别的题认为有一张图属于本题，请确认是否需要” stayed on the card.
                current_flags = [flag for flag in current_flags if flag not in DECISION_FLAGS]
                if not current_flags and base_state == Question.State.YELLOW:
                    base_state = Question.State.GREEN
            flags, state = qtypes.sync(current_flags, base_state, kind)
            if invented and state == Question.State.GREEN:
                state = Question.State.YELLOW
            before = (question.stem, dict(question.options or {}), question.question_type, question.origin,
                      question.answer, question.analysis, list(question.flags or []), question.state)
            if (stem, options, kind, origin, answer, analysis, flags, state) == before:
                # 1.10.2: store where the “MinerU 读法不同” spots are, once, so the
                # review page need not look them up again on every load.
                spotted = read_c_with_spots(question)
                if spotted is not None:
                    Question.all_objects.filter(pk=question.pk).update(read_c=spotted)
                continue
            approval_was_current = approval_is_current(question)
            question.stem, question.options, question.question_type = stem, options, kind
            question.origin, question.answer, question.analysis = origin, answer, analysis
            question.flags, question.state = flags, state
            spotted = read_c_with_spots(question)
            if spotted is not None:
                question.read_c = spotted
            for field in ("read_a", "read_b", "read_c"):
                setattr(question, field, _tidy_reading(getattr(question, field), switches, origin))
            if approval_was_current and approval_is_current_ignoring_hash(question):
                question.approved_content_hash = approval_hash(question)
            question.save(update_fields=[
                "stem", "options", "question_type", "origin", "answer", "analysis", "flags", "state",
                "read_a", "read_b", "read_c", "approved_content_hash", "updated_at",
            ])
            counts["questions"] += 1

        for publication in PublishedQuestion.objects.select_for_update():
            content = deepcopy(publication.content)
            original = (content.get("stem") or "", dict(content.get("options") or {}),
                        content.get("question_type") or "unknown", str(content.get("origin") or ""),
                        content.get("answer") or "", content.get("analysis") or "")
            stem, options, kind, origin = tidy_text(*original[:4], switches=switches)
            answer = prose.tidy_value(original[4], switches=switches)
            analysis = prose.tidy_value(original[5], switches=switches)
            if (stem, options, kind, origin, answer, analysis) == original:
                continue
            old_hash = publication.content_hash
            content["stem"], content["question_type"] = stem, kind
            content["answer"], content["analysis"] = answer, analysis
            if origin:
                content["origin"] = origin
            if content.get("options") or options:
                content["options"] = option_content(options)
            new_hash = content_hash(content)
            review = content.get("review")
            if isinstance(review, dict) and review.get("approved_content_hash") == old_hash:
                review["approved_content_hash"] = new_hash
            publication.content = content
            publication.content_hash = new_hash
            publication.question_type = kind
            publication.search_text = _search_text(content, publication.extras)
            publication.save(update_fields=["content", "content_hash", "question_type", "search_text"])
            counts["publications"] += 1
    return counts


def approval_is_current_ignoring_hash(question: Question) -> bool:
    """Whether the card could carry an approval at all (figures settled, type chosen…)."""
    if blocks_approval(stored_or_derived_review(question)) or type_blocks_approval(question):
        return False
    return bool(question.approved and question.state in REVIEWABLE_STATES and question.stem.strip())


# 1.5.1 name for the same cleanup.
strip_saved_example_labels = tidy_saved_cards

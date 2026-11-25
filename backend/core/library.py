"""正式题库：把终审通过的题卡保存成不可变快照（与 M3 题库页面的数据格式一致）。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
import uuid
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import demo, features, imaging, prose, qtypes, source_images
from .figure_policy import CONFIRMED_NO_FIGURE, DECISION_FLAGS, blocking_message, blocks_approval
from .models import LibrarySolution, Paper, PublishedQuestion, Question, QuestionGroup
from .textnorm import strip_example_label, strip_type_label

CHOICE_TYPES = set(qtypes.CHOICE_TYPES)
OPTION_KEYS = ("A", "B", "C", "D", "E", "F", "G", "H")
# 每道选择题固定保留 A–D 以保持既有四选题哈希完全兼容；E–H 按需仅在非空时写入。
BASE_OPTION_KEYS = ("A", "B", "C", "D")
EXTRA_OPTION_KEYS = ("E", "F", "G", "H")


def option_content(options: dict | None, *, plain: bool = False) -> dict:
    options = options or {}
    text = prose.plain_markup if plain else (lambda value: value)
    content = {key: text(options.get(key, "")) for key in BASE_OPTION_KEYS}
    for key in EXTRA_OPTION_KEYS:
        if str(options.get(key) or "").strip():
            content[key] = text(options[key])
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
        if isinstance(part, dict)
        and isinstance(part.get("page_idx"), int)
        and isinstance(part.get("bbox"), list)
        and len(part["bbox"]) == 4
    ]


def figure_identity(figure: dict) -> str:
    """What the cropped image depends on.  A one-piece figure keeps its old key."""
    word_asset = figure.get("word_asset") if isinstance(figure, dict) else None
    if isinstance(word_asset, str) and word_asset:
        return json.dumps(["word", word_asset])
    parts = figure_parts(figure)
    if not parts:
        return json.dumps([figure["page_idx"], figure["bbox"]])
    return json.dumps([[figure["page_idx"], figure["bbox"]]] + [[p["page_idx"], p["bbox"]] for p in parts])


def figure_file(question: Question, index: int) -> Path:
    """按题卡保存的配图坐标从高清原页裁图（缓存，坐标没变就不重裁）。

    裁图用 :func:`imaging.crop_figure`：框边正好压在某行字上时（教材里最常见的是
    图注「第 N 题」），严格照框裁会把那行字切成半截，印在卷子上看得见。让开一点
    就不切了。已经裁好的图还在缓存里，不会被改动；重裁要删 ``<paper>/figures``。
    """
    from .pipeline import PageStore, paper_dir

    figure = question.figures[index]
    word_asset = figure.get("word_asset") if isinstance(figure, dict) else None
    if isinstance(word_asset, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", word_asset):
        embedded = paper_dir(question.paper) / "word-figures" / word_asset
        if embedded.is_file():
            return embedded
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
            # 多片是同一张图被页断切开，每片的余量必须一样大，否则拼回去列对不齐。
            image = imaging.stack_figure_pieces(pieces)
            image.save(target, format="PNG", optimize=True)
        else:
            page = store.load(figure["page_idx"])
            imaging.crop_figure(page, imaging.to_pixels(figure["bbox"], page.size)).save(
                target, format="PNG", optimize=True
            )
    return target


def _content_figure(figure: dict) -> dict:
    item = {
        "slot": figure["slot"],
        "page_idx": figure["page_idx"],
        "bbox": figure["bbox"],
        "source": figure.get("source", "unknown"),
    }
    parts = figure_parts(figure)
    if parts:
        item["parts"] = parts
    return item


def final_content(question: Question) -> dict:
    is_choice = question.question_type in CHOICE_TYPES or bool(question.options)
    source_origin = (
        "manual"
        if question.start_source == "manual" or question.regions != question.regions_auto
        else question.start_source
    )
    # 入库版是要印给人看的，所以这里再清一次识读标记。早于清理规则的旧题，
    # 题卡里留着“**做一做**”“<!-- Image(268, 493, 650, 637) -->”这样的原文，
    # 审核时看得见原样，印出去的卷子上不该有它们。题卡本身一个字不改。
    content = {
        "number": question.number,
        "section": prose.plain_markup(question.section or ""),
        "question_type": question.question_type,
        "stem": prose.plain_markup(question.stem or ""),
        "options": option_content(question.options, plain=True) if is_choice else {},
        "answer": prose.plain_markup(question.answer or ""),
        "analysis": prose.plain_markup(question.analysis or ""),
        "origin": prose.plain_markup(question.origin or ""),
        "figures": [_content_figure(f) for f in question.figures],
        "sources": [
            {"page_idx": r["page_idx"], "bbox": r["bbox"], "type": "text", "source": source_origin}
            for r in question.regions
        ],
        "document_id": str(question.paper_id),
        "source_filename": question.paper.display_name,
        "source_group": (
            {"id": question.group_id, "title": question.group.display_title(), "sequence": question.group.sequence}
            if question.group_id
            else None
        ),
        # 审核记录随不可变快照保存。一般提示不参与版本身份；人工“确实无图”的决定例外，
        # 因为它是允许一条原本会被阻止的题目入库的关键依据。
        "review": {
            "state": question.state,
            "flags": list(question.flags or []),
            "approved_at": question.approved_at.isoformat() if question.approved_at else None,
            "approved_content_hash": question.approved_content_hash,
            "text_source": question.text_source,
            "edited": question.edited,
            "figure_review": deepcopy(source_images.review(question)),
        },
    }
    if source_images.is_image(question):
        content["body_mode"] = "source_image"
        content["question_images"] = source_images.assets(question)
        # The complete body crop already includes all original illustrations.
        content["figures"] = []
    return content


def content_hash(content: dict) -> str:
    """题目内容与追溯来源的稳定哈希；忽略图片 URL 等发布时生成的字段。"""
    material = {
        k: content.get(k)
        for k in (
            "number",
            "section",
            "question_type",
            "stem",
            "options",
            "answer",
            "analysis",
            "document_id",
            "source_filename",
        )
    }
    material["figures"] = [
        {k: f.get(k) for k in ("slot", "page_idx", "bbox", "source")}
        # Only a stitched figure carries parts, so every other hash is unchanged.
        | ({"parts": figure_parts(f)} if figure_parts(f) else {})
        for f in content.get("figures", [])
    ]
    material["sources"] = [
        {k: source.get(k) for k in ("page_idx", "bbox", "type", "source")} for source in content.get("sources", [])
    ]
    if content.get("body_mode") == "source_image":
        material["body_mode"] = "source_image"
        material["question_images"] = [
            {
                key: item.get(key)
                for key in ("page_idx", "bbox", "order", "source", "render_sha256", "image_sha256", "width", "height")
            }
            for item in content.get("question_images", [])
        ]
    # 题源为空时不进校验：升级前通过、入库的题，校验值和以前一模一样。
    if str(content.get("origin") or "").strip():
        material["origin"] = content["origin"]
    figure_review = (content.get("review") or {}).get("figure_review") or {}
    if figure_review.get("status") == CONFIRMED_NO_FIGURE and figure_review.get("source") == "human":
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
        status=PublishedQuestion.Status.PUBLISHED,
        review_source="ai",
        content_hash=content_hash(final_content(question)),
    ).update(review_source="human", review_agent="")


def type_blocks_approval(question: Question) -> bool:
    """文字题题型还没定不能通过入库；原图题允许带「题型未定」先入库，后续在题库随时补选。"""
    if source_images.is_image(question):
        return False
    return not qtypes.decided(question.question_type)


def approval_is_current(question: Question) -> bool:
    if blocks_approval(source_images.review(question)) or type_blocks_approval(question):
        return False
    return bool(
        question.approved
        and question.approved_content_hash
        and question.state in REVIEWABLE_STATES
        and source_images.body_valid(question)
        and question.approved_content_hash == approval_hash(question)
    )


_LATEX_SYMBOLS = {
    "angle": "∠",
    "parallel": "∥",
    "perp": "⊥",
    "bot": "⊥",
    "triangle": "△",
    "times": "×",
    "div": "÷",
    "leqslant": "≤",
    "geqslant": "≥",
    "leq": "≤",
    "le": "≤",
    "geq": "≥",
    "ge": "≥",
    "neq": "≠",
    "ne": "≠",
    "pi": "π",
    "circ": "°",
    "sqrt": "√",
    "cdot": "·",
    "in": "∈",
    "cup": "∪",
    "cap": "∩",
    "infty": "∞",
    "mid": "|",
    "pm": "±",
    "because": "∵",
    "therefore": "∴",
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "theta": "θ",
    "square": "□",
    "cong": "≅",
    "sim": "∽",
    "forall": "∀",
    "exists": "∃",
    "emptyset": "∅",
    "varnothing": "∅",
    "subset": "⊂",
    "subseteq": "⊆",
    # 这几个是 KaTeX 排出来就长这样的正体英文函数名。表里没有它们，``replace``
    # 会把命令整个删掉（``\\sin A`` 变成 ``a``），于是卡面上明明印着 sin 30°，
    # 老师搜「sin」却一张都搜不到——不报错、不提示，只是静静地少一批题。
    # 只收真库里真出现过的：``\\sec`` 之类一次都没用到，收了反而可能撞上英文单词
    # （section 里有 sec）。正体函数名保持和屏幕上一致，搜到就是搜到。
    "sin": "sin",
    "cos": "cos",
    "tan": "tan",
    "log": "log",
    "ln": "ln",
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


SECTION_LABEL_MAX = 12
SECTION_LABEL_MAX_NUMBERED = 20
SECTION_LABEL_STOP = re.compile(r"[：:，,。;；]")
SECTION_LABEL_TITLE_STOP = re.compile(r"[：:，,。;；、！!？?…—～~]")
SECTION_NUMBERED = re.compile(r"^(\d+(?:\.\d+)+)\s*(.*)$")
SECTION_HAS_HAN = re.compile(r"[\u4e00-\u9fff]")


def first_page(regions) -> int | None:
    """这道题在原卷上第一次出现的那一页（一基）。

    原图位置可能横跨两页，这道题是从前面那页开始的，所以取最小的那页。
    全库只认这一条规矩：结果面板、回收站、题库卡片墙报的页码都得一样，
    不能一处按第一个框、一处按最小值。
    """
    pages = []
    for region in regions or []:
        if not isinstance(region, dict):
            continue
        index = region.get("page_idx")
        if isinstance(index, int) and index >= 0:
            pages.append(index + 1)
    return min(pages) if pages else None


def section_label(section) -> str:
    """卡面上真的印出来的那段章节名，空串表示卡面不显示。

    甲、短标签：教材的「习题 6.1」「复习题」短到 12 字、也不带冒号逗号，印在卡面上。
    乙、教材的小节名：「5.5.2 简单的三角恒等变换」这种，前面是多级点号编号
        （4.5.3），后面是中文标题、标题里没有句读。教材的小节名本来就长，
        只按 12 字卡会把整段丢掉（1.26.44 修的），丢掉就等于没定位。
    丙、一律不显示：试卷的 section 是一整段大题说明（「一、选择题：本大题共8
        小题，每小题5分…」）、数字噪音（「1.178 097 245.」）、一整句话
        （「1. 观察实际情景，发现和提出问题」）。那种一个字都不显示，也不该进
        搜索——它带着「本题共 N 小题」这类套话，进了搜索会把一堆不相干的卷子
        都搜出来。
    前端卡面用同一套规则切出章节名（frontend/question-locator.js 的 sectionLabel），
    两边都拿同一份 fixture 断言，改一边就会红。
    """
    text = str(section or "").strip()
    if not text:
        return ""
    if "\n" in text or "\r" in text:
        return ""
    if len(text) <= SECTION_LABEL_MAX and not SECTION_LABEL_STOP.search(text):
        return text
    matched = SECTION_NUMBERED.match(text)
    if matched:
        title = matched.group(2).strip()
        if (
            len(text) <= SECTION_LABEL_MAX_NUMBERED
            and title
            and SECTION_HAS_HAN.search(title)
            and not SECTION_LABEL_TITLE_STOP.search(title)
        ):
            return text
    return ""


def _search_text(content: dict, extras: dict | None = None) -> str:
    options = content.get("options") or {}
    option_values = options.values() if isinstance(options, dict) else []
    parts = [
        content.get("stem") or "",
        *option_values,
        content.get("answer") or "",
        content.get("analysis") or "",
        content.get("source_filename") or "",
        (content.get("source_group") or {}).get("title", ""),
        section_label(content.get("section")),
        str(content.get("origin") or ""),
        *tags_of(extras),
    ]
    return search_key(" ".join(str(part) for part in parts))


def tags_of(extras: dict | None) -> list[str]:
    tags = (extras or {}).get("tags")
    return [str(tag) for tag in tags if str(tag).strip()] if isinstance(tags, list) else []


def tags_text(tags) -> str:
    """“|甲|乙|”：一个标签一段，筛选时整段匹配，“函数”不会命中“函数的应用”。"""
    cleaned = [str(tag).replace("|", " ").strip() for tag in (tags or []) if str(tag).strip()]
    return f"|{'|'.join(cleaned)}|" if cleaned else ""


def generation_fingerprint(content: dict, publication_id=None) -> str:
    """Bind generated extras to task text, crop identity and actual image bytes.

    A publication's download URL changes between versions, but that alone is
    not a new mathematical task. Image bytes and crop metadata must both match.
    Paths are restricted to this publication's immutable local asset folder.
    """
    material = {key: deepcopy(content.get(key)) for key in ("question_type", "stem", "options")}
    if content.get("body_mode") == "source_image":
        material["body_mode"] = "source_image"
        material["question_images"] = [
            {key: deepcopy(item.get(key)) for key in ("page_idx", "bbox", "order", "image_sha256")}
            for item in content.get("question_images", [])
        ]
        for item, image in zip(material["question_images"], content.get("question_images", [])):
            owner = str(publication_id or "")
            if not owner:
                match = re.fullmatch(
                    r"/api/library/([0-9a-fA-F-]{36})/question-images/[^/]+", str(image.get("url") or "")
                )
                owner = match.group(1) if match else ""
            name = str(image.get("file") or "")
            try:
                owner = str(uuid.UUID(owner))
                if not re.fullmatch(r"question-\d{1,2}\.png", name):
                    raise ValueError
                actual = hashlib.sha256((Path(settings.DATA_ROOT) / "library" / owner / name).read_bytes()).hexdigest()
                if actual != image.get("image_sha256"):
                    item["invalid"] = True
                item["actual_sha256"] = actual
            except (ValueError, OSError):
                item["missing"] = True
    images = []
    for figure in content.get("figures") or []:
        if not isinstance(figure, dict):
            images.append({"invalid": True})
            continue
        image = {key: deepcopy(figure.get(key)) for key in ("slot", "page_idx", "bbox", "parts", "source")}
        name = str(figure.get("file") or "")
        owner = str(publication_id or "")
        if not owner:
            match = re.fullmatch(r"/api/library/([0-9a-fA-F-]{36})/figures/[^/]+", str(figure.get("url") or ""))
            owner = match.group(1) if match else ""
        try:
            owner = str(uuid.UUID(owner))
            if not name or Path(name).name != name or "/" in name or "\\" in name:
                raise ValueError
            data = (Path(settings.DATA_ROOT) / "library" / owner / name).read_bytes()
            image["sha256"] = hashlib.sha256(data).hexdigest()
        except (OSError, ValueError):
            image["missing"] = True
        images.append(image)
    material["figures"] = images
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def carried_extras(previous: PublishedQuestion | None, content: dict) -> dict:
    """Inherit extras only for the same task; legacy answers stay on their source version."""
    if previous is None or not isinstance(previous.extras, dict):
        return {}
    extras = {}
    old_fingerprint = generation_fingerprint(previous.content or {}, previous.id)
    same_task = old_fingerprint == generation_fingerprint(content)
    tag_binding = previous.extras.get("tags_fingerprint")
    if same_task and tags_of(previous.extras) and (not tag_binding or tag_binding == old_fingerprint):
        extras["tags"] = tags_of(previous.extras)
        if previous.extras.get("tags_source"):
            extras["tags_source"] = previous.extras["tags_source"]
        for key in (
            "tags_at",
            "tags_fingerprint",
            "tags_publication_id",
            "tags_agent",
            "tags_executor",
            "tags_checked",
        ):
            if key in previous.extras:
                extras[key] = previous.extras[key]
    answer = previous.extras.get("ai_answer")
    if same_task and isinstance(answer, dict) and answer.get("fingerprint") == old_fingerprint:
        # Fingerprint binding is evidence of which task was generated, never
        # evidence that the mathematical answer has been checked by a person.
        extras["ai_answer"] = deepcopy(answer)
    if "onenote" in previous.extras:
        extras["onenote"] = deepcopy(previous.extras["onenote"])
    for key in ("evidence_sources", "variant_group_id", "dismissed_similarity_ids"):
        if key in previous.extras:
            extras[key] = deepcopy(previous.extras[key])
    if previous.extras.get("solution_id"):
        # The old immutable solution and its assets stay with the old version.
        # Never silently apply an edited solution to a replacement question.
        extras["solution_needs_review_id"] = previous.extras["solution_id"]
    return extras


def needs_tags(extras: dict | None) -> bool:
    return not tags_of(extras)


def has_saved_solution(extras: dict | None) -> bool:
    """判断入库题是否绑定了已保存的解析版本。"""
    return isinstance(extras, dict) and bool(str(extras.get("solution_id") or "").strip())


def has_formal_answer(content: dict | None, extras: dict | None) -> bool:
    """判断入库题是否已有正式答案（原卷答案或已保存解析版本）。"""
    return bool(str((content or {}).get("answer") or "").strip()) or has_saved_solution(extras)


def needs_answer(extras: dict | None, content: dict | None) -> bool:
    return not (has_formal_answer(content, extras) or bool((extras or {}).get("ai_answer")))


def generation_backlog() -> dict:
    """题库里还差几道才补齐：{tags, answer, total}。

    生成要花钱，开关旁边就得摆着数字，让用户看着数字决定，而不是点下去才知道。
    这里只数「还差什么」，不数「正在跑」——正在跑的由任务列表说。
    判据和 ``library_jobs.queue_on_intake`` 是同一套，别各数一遍。
    """
    live = PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED)
    tags = answer = 0
    for extras, content in live.values_list("extras", "content"):
        tags += needs_tags(extras)
        answer += needs_answer(extras, content)
    return {"tags": tags, "answer": answer, "total": live.count()}


def save_extras(publication: PublishedQuestion, extras: dict) -> PublishedQuestion:
    """Store tags / AI answer on a library entry: no new version, no re-approval."""
    # The tag editor and answer editor are independent sidecar writes. Preserve
    # the latest tag projection when an answer editor submitted an older copy
    # of extras, and preserve the latest answer when a tag operation refreshes
    # its projection.
    current = PublishedQuestion.objects.filter(pk=publication.pk).values("extras").first()
    saved = current.get("extras") if current and isinstance(current.get("extras"), dict) else {}
    extras = deepcopy(extras) if isinstance(extras, dict) else {}
    preserve_keys = (
        "tags",
        "tags_source",
        "tags_at",
        "tags_fingerprint",
        "tags_publication_id",
        "tags_agent",
        "tags_executor",
        "tags_checked",
        "tags_legacy_original",
        "evidence_sources",
        "variant_group_id",
        "dismissed_similarity_ids",
    )
    for key in preserve_keys:
        if key in saved:
            extras[key] = deepcopy(saved[key])
        else:
            extras.pop(key, None)
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
    return (
        bool(question.approved_content_hash)
        and latest.content_hash == question.approved_content_hash
        and approval_is_current(question)
    )


def publish(question: Question, *, queue_enrichment: bool = True) -> tuple[PublishedQuestion, bool]:
    """入库一题。内容没变就不重复生成版本。返回 (快照, 是否新建)。"""
    with transaction.atomic():
        try:
            paper = Paper.objects.select_for_update().get(pk=question.paper_id)
        except Paper.DoesNotExist:
            raise ValueError("这份试卷任务已删除，不能再入库") from None
        if demo.is_demo(paper):
            raise ValueError(demo.PUBLISH_REFUSED)
        try:
            question = Question.objects.select_for_update().select_related("paper").get(pk=question.pk)
        except Question.DoesNotExist:
            raise ValueError("这道题已放入回收站，请恢复后再入库") from None
        if not question.approved:
            raise ValueError(f"第 {question.number} 题还没有通过终审")
        if question.state not in REVIEWABLE_STATES:
            raise ValueError(f"第 {question.number} 题当前状态不能入库，请先完成识读或人工修正")
        if not source_images.body_valid(question):
            raise ValueError(f"第 {question.number} 题没有有效正文或原卷裁片")
        figure_review = source_images.review(question)
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
            for index, image in enumerate(content.get("question_images", [])):
                name = f"question-{index + 1}.png"
                original = Path(settings.DATA_ROOT) / str(question.paper_id) / "question-images" / image["file"]
                shutil.copyfile(original, folder / name)
                if hashlib.sha256((folder / name).read_bytes()).hexdigest() != image["image_sha256"]:
                    raise ValueError("原卷裁片已变化，请重新终审")
                image["file"] = name
                image["url"] = f"/api/library/{publication_id}/question-images/{name}"
            for index, figure in enumerate(content["figures"]):
                name = f"figure-{index + 1}.png"
                shutil.copyfile(figure_file(question, index), folder / name)
                figure["file"] = name
                figure["url"] = f"/api/library/{publication_id}/figures/{name}"
            extras = carried_extras(latest, content)
            if latest is not None and extras.get("evidence_sources"):
                prev_dir = settings.DATA_ROOT / "library" / str(latest.pk)
                carried_sources = []
                for ev in extras["evidence_sources"]:
                    if not isinstance(ev, dict):
                        continue
                    ev_copy = deepcopy(ev)
                    crop_file = str(ev_copy.get("crop_file") or "").strip()
                    ev_id = str(ev_copy.get("id") or "").strip()
                    if crop_file:
                        src_crop = prev_dir / crop_file
                        if src_crop.is_file():
                            shutil.copyfile(src_crop, folder / crop_file)
                            ev_copy["crop_url"] = f"/api/library/{publication_id}/evidence/{ev_id}.png"
                        else:
                            ev_copy["crop_file"] = ""
                            ev_copy["crop_url"] = ""
                    carried_sources.append(ev_copy)
                extras["evidence_sources"] = carried_sources
            publication = PublishedQuestion.objects.create(
                id=publication_id,
                question=question,
                paper=paper,
                source_filename=paper.display_name,
                number=question.number,
                question_type=question.question_type,
                version=(latest.version if latest else 0) + 1,
                content=content,
                content_hash=digest,
                search_text=_search_text(content, extras),
                review_source=source,
                review_agent=agent,
                extras=extras,
                tags_text=tags_text(tags_of(extras)),
            )
            from . import library_fts

            superseded_ids = list(
                question.publications.filter(status=PublishedQuestion.Status.PUBLISHED)
                .exclude(pk=publication.pk)
                .values_list("id", flat=True)
            )
            if superseded_ids:
                PublishedQuestion.objects.filter(id__in=superseded_ids).update(
                    status=PublishedQuestion.Status.SUPERSEDED
                )
                library_fts.delete_publications(superseded_ids)
            _materialize_word_solution_figures(question, publication)
            from . import library_similarity

            bind_peer = (
                str((question.read_a or {}).get("bind_variant_pub_id") or "").strip()
                if isinstance(question.read_a, dict)
                else ""
            )
            if bind_peer:
                try:
                    library_similarity.bind_variant_group([str(publication.pk), bind_peer])
                    publication.refresh_from_db()
                except library_similarity.SimilarityError:
                    pass
            transaction.on_commit(library_similarity.bump_similarity_revision)
            # A queued or running enrichment job keeps its original snapshot.
            # The worker rejects replaced versions; a new version needs its own job.
            if queue_enrichment:
                from .library_jobs import queue_on_intake

                queue_on_intake(publication)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            shutil.rmtree(settings.DATA_ROOT / "library-solutions" / str(publication_id), ignore_errors=True)
            raise
    return publication, True


def _materialize_word_solution_figures(question: Question, publication: PublishedQuestion) -> None:
    """将 Word 直读拆出的解析内嵌配图自动落入正式题库解析版本层（LibrarySolution）。"""
    read_meta = question.read_a if isinstance(question.read_a, dict) else {}
    raw_items = read_meta.get("solution_figures")
    if not isinstance(raw_items, list) or not raw_items:
        return
    if (publication.extras or {}).get("solution_id"):
        return
    from .pipeline import paper_dir

    src_dir = paper_dir(question.paper) / "word-solution-figures"
    if not src_dir.is_dir():
        return
    dest_dir = Path(settings.DATA_ROOT) / "library-solutions" / str(publication.pk)
    normalized_figs: list[dict] = []
    for item in raw_items[:24]:
        if not isinstance(item, dict):
            continue
        fname = item.get("file")
        width = item.get("width")
        height = item.get("height")
        sha = item.get("sha256")
        if (
            not isinstance(fname, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", fname)
            or type(width) is not int
            or type(height) is not int
            or not isinstance(sha, str)
            or len(sha) != 64
        ):
            continue
        src_file = src_dir / fname
        if not src_file.is_file():
            continue
        data = src_file.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        asset_id = str(uuid.uuid4())
        (dest_dir / f"{asset_id}.png").write_bytes(data)
        spec = {"id": asset_id, "publication_id": str(publication.pk), "sha256": sha, "width": width, "height": height}
        (dest_dir / f"{asset_id}.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        disp_w = round(min(178.0, max(20.0, width * 25.4 / 150.0)), 1)
        normalized_figs.append({"id": asset_id, "display_width": disp_w, "position": "after", "paragraph": 0})
    if not normalized_figs:
        return
    content = publication.content or {}
    row = LibrarySolution.objects.create(
        publication=publication,
        parent=None,
        fingerprint=generation_fingerprint(content, publication.pk),
        answer=str(content.get("answer") or ""),
        analysis=str(content.get("analysis") or ""),
        figures=normalized_figs,
    )
    save_extras(publication, {**deepcopy(publication.extras or {}), "solution_id": str(row.pk)})


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

        questions = list(Question.all_objects.select_for_update().select_related("paper", "group").filter(paper=paper))
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
                group.title = f"{name}{group.title[len(old_name) :]}"[:255]
            else:
                continue
            group.updated_at = timezone.now()
            renamed_groups[group.pk] = group.title
        if renamed_groups:
            QuestionGroup.objects.bulk_update(
                [group for group in groups if group.pk in renamed_groups], ["title", "updated_at"]
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
            from . import library_fts

            PublishedQuestion.objects.bulk_update(
                publications, ["source_filename", "content", "content_hash", "search_text"]
            )
            library_fts.upsert_publications(publications)
    return paper, True


def set_paper_material_type(paper: Paper, material_type: str) -> tuple[Paper, bool]:
    """切换资料类型（exam 试卷 / book 教材教辅），不改动题卡与已入库版本。"""
    if material_type not in Paper.MaterialType.values:
        raise ValueError("资料类型只能是 exam（试卷）或 book（教材）")
    with transaction.atomic():
        paper = Paper.objects.select_for_update().get(pk=paper.pk)
        if paper.material_type == material_type:
            return paper, False
        paper.material_type = material_type
        paper.save(update_fields=["material_type", "updated_at"])
    return paper, True


def update_published_sections(section_by_pub_id: dict[str, str]) -> tuple[int, dict[str, str]]:
    """批量修改已入库题目的章节小节归属（section），保持原审核状态且不生成新版本。

    返回 (实际变更条数, {publication_id: 修改前 section}) 供撤销恢复。
    """
    if not section_by_pub_id:
        return 0, {}
    ordered_ids = list(section_by_pub_id.keys())
    before_sections: dict[str, str] = {}
    updated_publications: list[PublishedQuestion] = []
    updated_questions: list[Question] = []
    with transaction.atomic():
        publications = {
            str(pub.pk): pub
            for pub in PublishedQuestion.objects.select_for_update().filter(
                pk__in=ordered_ids, status=PublishedQuestion.Status.PUBLISHED
            )
        }
        missing = [pid for pid in ordered_ids if pid not in publications]
        if missing:
            raise ValueError(f"找不到已入库题目：{missing[0]}")
        question_ids = [pub.question_id for pub in publications.values() if pub.question_id]
        questions_by_id = {
            q.pk: q
            for q in Question.all_objects.select_for_update()
            .select_related("paper", "group")
            .filter(pk__in=question_ids)
        }
        for pub_id in ordered_ids:
            pub = publications[pub_id]
            raw_section = str(section_by_pub_id[pub_id] or "").strip()[:120]
            clean_section = prose.plain_markup(raw_section)
            content = deepcopy(pub.content) if isinstance(pub.content, dict) else {}
            old_section = str(content.get("section") or "")
            question = questions_by_id.get(pub.question_id) if pub.question_id else None
            before_sections[pub_id] = question.section if question is not None else old_section
            if old_section == clean_section and (question is None or question.section == raw_section):
                continue
            old_hash = pub.content_hash
            content["section"] = clean_section
            new_hash = content_hash(content)
            review = content.get("review")
            if isinstance(review, dict) and review.get("approved_content_hash") == old_hash:
                review["approved_content_hash"] = new_hash
            pub.content = content
            pub.content_hash = new_hash
            pub.search_text = _search_text(content, pub.extras)
            updated_publications.append(pub)
            if question is not None:
                was_current = approval_is_current(question) and question.approved_content_hash == old_hash
                question.section = raw_section
                if was_current:
                    question.approved_content_hash = approval_hash(question)
                updated_questions.append(question)
        if updated_questions:
            Question.all_objects.bulk_update(updated_questions, ["section", "approved_content_hash"])
        if updated_publications:
            from . import library_fts

            PublishedQuestion.objects.bulk_update(updated_publications, ["content", "content_hash", "search_text"])
            library_fts.upsert_publications(updated_publications)
    return len(updated_publications), before_sections


LIVE_PUBLICATION_FIELDS = ("id", "question_id", "version", "content_hash", "status")


def live_publications_prefetch():
    """For a list of cards: each card's published versions in one query (``publication_state`` uses it)."""
    from django.db.models import Prefetch

    return Prefetch(
        "publications",
        queryset=PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED)
        .only(*LIVE_PUBLICATION_FIELDS)
        .order_by("-version"),
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
    return {
        "id": str(live.id),
        "version": live.version,
        "up_to_date": live.content_hash == content_hash(final_content(question)),
    }


def withdraw(publication: PublishedQuestion) -> PublishedQuestion:
    if publication.status == PublishedQuestion.Status.PUBLISHED:
        publication.status = PublishedQuestion.Status.WITHDRAWN
        publication.withdrawn_at = timezone.now()
        publication.save(update_fields=["status", "withdrawn_at"])
        from . import library_similarity

        library_similarity.bump_similarity_revision()
    return publication


def _content_with_figure_state(publication: PublishedQuestion) -> dict:
    """题面里的配图文件和题面分开存。换机器、原卷被挪走或清理过之后，
    库里的记录还在、文件已经不在了 —— 这时界面不该去取一张必然 404 的图，
    也不该只留个裂图，标出来由界面照实说。"""
    content = publication.content or {}
    figures = content.get("figures") or []
    if not figures:
        return content
    folder = settings.DATA_ROOT / "library" / str(publication.pk)
    missing = set()
    for index, figure in enumerate(figures):
        if not isinstance(figure, dict):
            continue
        name = figure.get("file") or f"figure-{index + 1}.png"
        if not (folder / str(name)).is_file():
            missing.add(index)
    if not missing:
        return content
    marked = deepcopy(content)
    for index in missing:
        marked["figures"][index]["missing"] = True
    return marked


_LOCATOR_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LOCATOR_SHORT_RE = re.compile(r"(?:^|[^0-9a-fA-F])Q-([0-9a-fA-F]{6,32})\b", re.IGNORECASE)


def short_id_for(publication_id, length: int = 8) -> str:
    """生成题目“身份证”短定位码（形如 Q-c65f1f5f），供用户复制并由 AI Agent 快速定位。"""
    hex_id = str(publication_id).replace("-", "").lower()
    return f"Q-{hex_id[:length]}"


def question_locator(publication: PublishedQuestion, *, short_id: str | None = None) -> dict:
    """构造题目的结构化位置卡片，同时包含可读题源、短定位码与可复制定位串。"""
    sid = short_id or short_id_for(publication.pk)
    title = str(publication.source_filename or "").strip() or "未命名资料"
    return {
        "short_id": sid,
        "id": str(publication.pk),
        "source": title,
        "number": publication.number,
        "version": publication.version,
        "url": f"/eagle?q={publication.pk}",
        "copy_text": f"[题有据题目 {sid}] 《{title}》第{publication.number}题 (id: {publication.pk})",
    }


def resolve_publication_ref(raw_ref: str) -> PublishedQuestion:
    """从 UUID、短码（Q-xxxxxxxx）或整段复制定位文本中解析唯一的已入库题目。"""
    text = str(raw_ref or "").strip()
    if not text:
        raise ValueError("请提供题目定位码或 UUID。")

    uuid_match = _LOCATOR_UUID_RE.search(text)
    if uuid_match:
        target_uuid = uuid.UUID(uuid_match.group(0))
        pub = PublishedQuestion.objects.filter(pk=target_uuid).first()
        if pub is None:
            raise ValueError(f"未找到编号为 {target_uuid} 的题库题目。")
        return pub

    short_match = _LOCATOR_SHORT_RE.search(text)
    prefix = (
        (short_match.group(1) if short_match else text.removeprefix("Q-").removeprefix("q-")).replace("-", "").lower()
    )
    if not re.fullmatch(r"[0-9a-f]{6,32}", prefix):
        raise ValueError(f"无法识别的题目定位格式：{text}")

    candidates = list(
        PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).only(
            "id", "status", "source_filename", "number", "version"
        )
    )
    matched = [item for item in candidates if str(item.pk).replace("-", "").lower().startswith(prefix)]
    if not matched:
        # 兜底查全库（包含已替代或撤回版本），给出准确对象或引导
        all_candidates = list(PublishedQuestion.objects.all().only("id"))
        matched_ids = [item.pk for item in all_candidates if str(item.pk).replace("-", "").lower().startswith(prefix)]
        if len(matched_ids) == 1:
            return PublishedQuestion.objects.get(pk=matched_ids[0])
        if len(matched_ids) > 1:
            raise ValueError(f"定位前缀 {prefix} 匹配到 {len(matched_ids)} 条记录，请提供更长前缀或完整 UUID。")
        raise ValueError(f"题库中没有匹配 {text} 的题目。")
    if len(matched) > 1:
        raise ValueError(f"定位前缀 Q-{prefix} 匹配到 {len(matched)} 道题目，请改用完整 UUID。")
    return PublishedQuestion.objects.get(pk=matched[0].pk)


def _normalized_evidence_sources(publication_id, extras: dict | None) -> list[dict]:
    if not isinstance(extras, dict):
        return []
    out: list[dict] = []
    for item in extras.get("evidence_sources") or []:
        if not isinstance(item, dict):
            continue
        entry = deepcopy(item)
        crop_file = str(entry.get("crop_file") or "").strip()
        ev_id = str(entry.get("id") or "").strip()
        entry["crop_url"] = f"/api/library/{publication_id}/evidence/{ev_id}.png" if (crop_file and ev_id) else ""
        out.append(entry)
    return out


def _variant_peer_summary(publication: PublishedQuestion) -> dict:
    content = publication.content if isinstance(publication.content, dict) else {}
    stem = str(content.get("stem") or "").strip()
    return {
        "id": str(publication.pk),
        "short_id": short_id_for(publication.pk),
        "source_filename": str(publication.source_filename or ""),
        "number": publication.number,
        "question_type": publication.question_type,
        "stem_preview": stem[:120],
    }


def _variant_peers_map(publications: list[PublishedQuestion]) -> dict[str, list[dict]]:
    groups_by_pub: dict[str, str] = {}
    group_ids: set[str] = set()
    for pub in publications:
        vg = str((pub.extras or {}).get("variant_group_id") or "").strip() if isinstance(pub.extras, dict) else ""
        if vg:
            groups_by_pub[str(pub.pk)] = vg
            group_ids.add(vg)
    if not group_ids:
        return {}
    buckets: dict[str, list[dict]] = {}
    peers = PublishedQuestion.objects.filter(
        status=PublishedQuestion.Status.PUBLISHED, extras__variant_group_id__in=list(group_ids)
    ).only("id", "source_filename", "number", "question_type", "content", "extras")
    for peer in peers:
        vg = str((peer.extras or {}).get("variant_group_id") or "").strip() if isinstance(peer.extras, dict) else ""
        if vg:
            buckets.setdefault(vg, []).append(_variant_peer_summary(peer))
    out: dict[str, list[dict]] = {}
    for pub_id, vg in groups_by_pub.items():
        out[pub_id] = [item for item in buckets.get(vg, []) if item["id"] != pub_id]
    return out


def publication_json(
    publication: PublishedQuestion,
    *,
    include_tag_details: bool = True,
    prefetched_solutions: dict | None = None,
    prefetched_variant_peers: dict[str, list[dict]] | None = None,
    prefetched_usage: dict[str, dict] | None = None,
) -> dict:
    from . import library_solutions, library_usage

    solution, solution_error = None, ""
    raw_solution_id = (publication.extras or {}).get("solution_id") if isinstance(publication.extras, dict) else None
    if raw_solution_id:
        try:
            expected_fp = generation_fingerprint(publication.content, publication.pk)
            if prefetched_solutions is not None:
                key = library_solutions.identity(raw_solution_id)
                row = prefetched_solutions.get(key)
                if row is None or row.publication_id != publication.pk:
                    raise library_solutions.SolutionError("解析版本不属于这道题或已经不存在", 409)
                if row.fingerprint != expected_fp:
                    raise library_solutions.SolutionError("解析对应的题面或配图已变化，请重新核对并保存解析", 409)
            else:
                row = library_solutions.selected(publication)
            solution = library_solutions.solution_json(row, fingerprint=expected_fp)
        except library_solutions.SolutionError as error:
            solution_error = str(error)
    content = _content_with_figure_state(publication)
    locator = question_locator(publication)
    extras_dict = publication.extras if isinstance(publication.extras, dict) else {}
    variant_group_id = str(extras_dict.get("variant_group_id") or "").strip() or None
    if prefetched_variant_peers is not None:
        variant_peers = prefetched_variant_peers.get(str(publication.pk), [])
    else:
        variant_peers = _variant_peers_map([publication]).get(str(publication.pk), [])
    usage_map = prefetched_usage if prefetched_usage is not None else library_usage.question_usage_map()
    data = {
        "id": str(publication.id),
        "short_id": locator["short_id"],
        "locator": locator,
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
        "content": content,
        "page": first_page(content.get("sources")),
        "origin": str((publication.content or {}).get("origin") or ""),
        "has_answer": bool(str((publication.content or {}).get("answer") or "").strip()) or bool(solution),
        "subquestions": qtypes.subquestion_count((publication.content or {}).get("stem")),
        "tags": tags_of(publication.extras),
        "ai_answer": extras_dict.get("ai_answer"),
        "onenote": extras_dict.get("onenote"),
        "evidence_sources": _normalized_evidence_sources(publication.pk, extras_dict),
        "variant_group_id": variant_group_id,
        "variant_peers": variant_peers,
        "usage_summary": usage_map.get(str(publication.pk)),
        "solution": solution,
        "solution_needs_review": bool(solution_error or extras_dict.get("solution_needs_review_id")),
        "previous_solution_id": extras_dict.get("solution_needs_review_id"),
        "solution_error": solution_error,
    }
    if not include_tag_details:
        data.update(tags=[], tag_revision=publication.tag_revision, tag_assignments=[], tag_suggestion=None)
        return data
    from . import library_tagging

    return library_tagging.attach_to_publication_json(publication, data)


def publications_json(publications) -> list[dict]:
    """一页题一次查完标签关联和建议，别一道题一道题地查。

    1.26.47：题库页一次要出四十道题。原来每道题都要单独查自己的标签关联、
    AI 建议、退役标签名和上下级关系 —— 一百多次查库，页面得多等半秒。
    出来的内容跟一道题一道题查完全一样。
    """
    from . import library_tagging, library_usage

    publications = list(publications)
    solution_ids = []
    for item in publications:
        raw_sid = (item.extras or {}).get("solution_id") if isinstance(item.extras, dict) else None
        if raw_sid:
            try:
                solution_ids.append(uuid.UUID(str(raw_sid)))
            except (ValueError, AttributeError):
                continue
    prefetched_solutions = (
        {str(row.pk): row for row in LibrarySolution.objects.filter(pk__in=set(solution_ids))} if solution_ids else {}
    )
    prefetched_variant_peers = _variant_peers_map(publications)
    prefetched_usage = library_usage.question_usage_map()
    rows = [
        publication_json(
            item,
            include_tag_details=False,
            prefetched_solutions=prefetched_solutions,
            prefetched_variant_peers=prefetched_variant_peers,
            prefetched_usage=prefetched_usage,
        )
        for item in publications
    ]
    return library_tagging.attach_batch_to_publications(publications, rows)


HISTORY_FIELDS = (
    ("body_mode", "正文形式"),
    ("question_images", "原图正文"),
    ("stem", "题干"),
    ("options", "选项"),
    ("answer", "答案"),
    ("analysis", "解析"),
    ("origin", "题源"),
    ("question_type", "题型"),
    ("figures", "配图"),
    ("sources", "原卷位置"),
    ("number", "原卷题号"),
    ("section", "大题分组"),
)


def _history_value(content: dict, key: str):
    value = content.get(key)
    if key == "body_mode":
        return "原图正文" if value == "source_image" else "文字正文"
    if key == "question_images":
        return [
            {name: image.get(name) for name in ("page_idx", "bbox", "order", "image_sha256")} for image in (value or [])
        ]
    if key == "figures":
        # Every publication gets new file names/URLs. Compare the actual crop,
        # slot and provenance instead, including all cross-page pieces.
        return [
            {name: figure.get(name) for name in ("slot", "page_idx", "bbox", "source", "parts")}
            for figure in (value or [])
        ]
    if key == "sources":
        return {
            "document_id": content.get("document_id"),
            "regions": [
                {name: region.get(name) for name in ("page_idx", "bbox", "type", "source")} for region in (value or [])
            ],
        }
    if key == "options":
        return {name: str(text) for name, text in (value or {}).items() if str(text).strip()}
    return value if value is not None else ""


def publication_changes(before: PublishedQuestion, after: PublishedQuestion, *, with_text: bool = True) -> list[dict]:
    """Compare two stored snapshots; never read the mutable draft or AI extras."""
    changes = []
    for key, label in HISTORY_FIELDS:
        old = _history_value(before.content, key)
        new = _history_value(after.content, key)
        if old == new:
            continue
        field = {"key": key, "label": label}
        if with_text and key not in {"figures", "sources", "question_images"}:
            if key == "options":
                old = "\n".join(f"{name}. {old[name]}" for name in sorted(old))
                new = "\n".join(f"{name}. {new[name]}" for name in sorted(new))
            elif key == "question_type":
                old, new = qtypes.TYPE_LABELS.get(old, old), qtypes.TYPE_LABELS.get(new, new)
            old, new = str(old), str(new)
            field.update(before=old, after=new)
            # Bound character comparison for very long textbook questions.
            # The complete snapshot remains available in the rendered panels.
            if len(old) + len(new) <= 20_000:
                field["segments"] = [
                    {"kind": kind, "before": old[a:b], "after": new[c:d]}
                    for kind, a, b, c, d in SequenceMatcher(None, old, new).get_opcodes()
                ]
        changes.append(field)
    return changes


def publication_history(publication: PublishedQuestion) -> list[dict]:
    """All versions of this exact card, including the selected snapshot.

    Orphan snapshots must never be grouped merely because their question FK
    is NULL (nor by number or filename, which repeat across chapters).
    """
    rows = (
        list(PublishedQuestion.objects.filter(question_id=publication.question_id).order_by("version"))
        if publication.question_id
        else [publication]
    )
    history = []
    previous = None
    for row in rows:
        history.append(
            {
                "id": str(row.id),
                "version": row.version,
                "status": row.status,
                "published_at": row.published_at.isoformat(),
                "review": {"source": row.review_source or "human", "agent": row.review_agent},
                "changes": [field["label"] for field in publication_changes(previous, row, with_text=False)]
                if previous
                else [],
                "previous_id": str(previous.id) if previous else None,
            }
        )
        previous = row
    return list(reversed(history))


# Source discovery is deliberately separate from version identity.  A similar
# question in another paper is evidence to inspect, never a licence to move a
# review, merge a draft or compare unrelated version numbers.
RELATED_SOURCE_LIMIT = 20
_SOURCE_PUNCTUATION = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "．": ".",
        "；": ";",
        "：": ":",
        "！": "!",
        "？": "?",
        "（": "(",
        "）": ")",
        "［": "[",
        "］": "]",
        "｛": "{",
        "｝": "}",
        "“": '"',
        "”": '"',
        "＂": '"',
        "‘": "'",
        "’": "'",
        "＇": "'",
    }
)
_SOURCE_PROTECTED = re.compile(
    r"(`+)[\s\S]*?\1|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)"
    r"|(?<!\\)\$\$[\s\S]*?(?<!\\)\$\$|(?<!\\)\$(?:\\.|[^$])*?(?<!\\)\$"
)
_SOURCE_IMAGE = re.compile(r"!\[|<\s*(?:img|svg)\b|\\includegraphics\b|如图|图示|下图|右图|左图|见图|图中", re.I)
_CJK_OR_PUNCT = r"[\u3400-\u9fff,.;:!?()\[\]{}\"'、]"


def _source_match_text(value: str, *, possible: bool = False) -> list[list[str]]:
    """Fold prose typography only; mathematical/code bytes remain exact.

    NFKC is unsafe here: it turns squared units into ordinary digits.  Do not
    strip symbols, rewrite LaTeX commands, change option labels or join English
    words.  Tables and malformed math also keep their original text.
    """
    if re.search(r"(?m)^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s)", value):
        return [["literal", value]]
    pieces = []
    start = 0
    for match in _SOURCE_PROTECTED.finditer(value):
        pieces.append(["prose", value[start : match.start()]])
        pieces.append(["literal", match.group()])
        start = match.end()
    pieces.append(["prose", value[start:]])
    if any(re.search(r"(?<!\\)\$|\\[([]|\||`", text) for kind, text in pieces if kind == "prose"):
        return [["literal", value]]
    for piece in pieces:
        if piece[0] != "prose":
            if (
                possible
                and not piece[1].startswith("`")
                and not re.search(r"\\(?:text\w*|mbox|hbox|operatorname)\b", piece[1])
            ):
                # These two TeX presentation differences only suggest an
                # inspection.  Even a candidate never alters exact history.
                text = re.sub(r"\\mid\b", "|", piece[1])
                # The separator command needs a following token boundary,
                # whereas its literal spelling does not.  Fold horizontal
                # layout around this separator only, not other math spaces.
                text = re.sub(r"[ \t]*\|[ \t]*", "|", text)
                text = re.sub(r"_\{([A-Za-z])\}", r"_\1", text)
                # A single-letter TeX subscript consumes one character; the
                # following horizontal space is only math layout, not a name.
                piece[1] = re.sub(r"_([A-Za-z])[ \t]+(?=[A-Za-z(\\])", r"_\1", text)
            continue
        text = re.sub(r"\s+", " ", piece[1].translate(_SOURCE_PUNCTUATION)).strip()
        text = re.sub(rf"(?<={_CJK_OR_PUNCT}) +| +(?={_CJK_OR_PUNCT})", "", text)
        piece[1] = text
    return pieces


def source_match_fingerprint(publication: PublishedQuestion, *, possible: bool = False) -> str | None:
    """Conservative identity for a text-only stored question, without writes."""
    content = publication.content
    if (
        not isinstance(content, dict)
        or content.get("body_mode") == "source_image"
        or content.get("figures")
        or ("figures" in content and not isinstance(content["figures"], list))
    ):
        return None
    kind = content.get("question_type")
    stem, options = content.get("stem"), content.get("options", {})
    if (
        kind != publication.question_type
        or kind not in qtypes.TYPE_LABELS
        or kind == "unknown"
        or not isinstance(stem, str)
        or not stem.strip()
        or not isinstance(options, dict)
    ):
        return None
    if any(key not in OPTION_KEYS or not isinstance(value, str) for key, value in options.items()):
        return None
    options = {key: value for key, value in options.items() if value.strip()}
    if (kind in CHOICE_TYPES and len(options) < 2) or (kind not in CHOICE_TYPES and options):
        return None
    if any(_SOURCE_IMAGE.search(value) for value in [stem, *options.values()]):
        return None
    material = {
        "type": kind,
        "stem": _source_match_text(stem, possible=possible),
        "options": {key: _source_match_text(value, possible=possible) for key, value in options.items()},
    }
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _publication_document(publication: PublishedQuestion) -> str:
    """Use the saved document UUID even when the original paper was deleted."""
    content = publication.content if isinstance(publication.content, dict) else {}
    document = publication.paper_id or content.get("document_id")
    try:
        return str(uuid.UUID(str(document))) if document else ""
    except (TypeError, ValueError, AttributeError):
        return ""


def related_publication_sources(publication: PublishedQuestion) -> dict:
    """Other live papers with exact text, or explicitly unconfirmed candidates.

    Exact source matches and limited TeX-notation candidates are disjoint.  Each
    result retains its own question, source, review and complete version history.
    Missing original answers mean that paper supplied no answer.  Contradictory
    non-empty answers or analyses are never silently treated as one question.
    AI reference answers in mutable extras cannot decide this relation.
    """
    fingerprint, document = source_match_fingerprint(publication), _publication_document(publication)
    possible_fingerprint = source_match_fingerprint(publication, possible=True)
    result = {
        f"{name}{suffix}": (False if suffix == "_truncated" else 0 if suffix == "_count" else [])
        for name in ("related_sources", "possible_sources")
        for suffix in ("", "_count", "_truncated")
    }
    if not fingerprint or not document:
        return result
    candidates = (
        PublishedQuestion.objects.filter(
            status=PublishedQuestion.Status.PUBLISHED, question_type=publication.question_type
        )
        .exclude(pk=publication.pk)
        .order_by("source_filename", "number", "-version", "id")
    )
    if publication.question_id:
        candidates = candidates.exclude(question_id=publication.question_id)
    if publication.paper_id:
        candidates = candidates.exclude(paper_id=publication.paper_id)
    seen_cards = set()
    matches = {"related_sources": [], "possible_sources": []}
    for candidate in candidates.iterator(chunk_size=200):
        other_document = _publication_document(candidate)
        if not other_document or other_document == document:
            continue
        other_fingerprint = source_match_fingerprint(candidate)
        if not other_fingerprint:
            continue
        name = "related_sources" if other_fingerprint == fingerprint else "possible_sources"
        if name == "possible_sources" and source_match_fingerprint(candidate, possible=True) != possible_fingerprint:
            continue
        if any(
            str(publication.content.get(key) or "").strip()
            and str(candidate.content.get(key) or "").strip()
            and _source_match_text(str(publication.content[key])) != _source_match_text(str(candidate.content[key]))
            for key in ("answer", "analysis")
        ):
            continue
        card_identity = candidate.question_id or str(candidate.pk)
        if card_identity in seen_cards:
            continue
        seen_cards.add(card_identity)
        result[f"{name}_count"] += 1
        if len(matches[name]) < RELATED_SOURCE_LIMIT:
            matches[name].append(candidate)
    for name, rows in matches.items():
        result[f"{name}_truncated"] = result[f"{name}_count"] > len(rows)
    matched_rows = [row for rows in matches.values() for row in rows]
    if matched_rows:
        from django.db.models import Count

        counts = dict(
            PublishedQuestion.objects.filter(
                question_id__in=[row.question_id for row in matched_rows if row.question_id]
            )
            .values("question_id")
            .annotate(total=Count("id"))
            .values_list("question_id", "total")
        )
        for name, rows in matches.items():
            result[name] = [
                publication_json(row, include_tag_details=False) | {"version_count": counts.get(row.question_id, 1)}
                for row in rows
            ]
    return result


_E_TAG = re.compile(r"\s*【\s*E\s*】\s*")
_LABEL_SETS_TYPE = {"unknown", "", "single_choice", "multiple_choice"}


def tidy_text(
    stem: str, options: dict | None, question_type: str, origin: str = "", *, switches: dict | None = None
) -> tuple[str, dict, str, str]:
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
    new_options = {
        key: prose.tidy_value(value, switches=switches) if isinstance(value, str) else value
        for key, value in new_options.items()
    }
    return new_stem, new_options, new_type, new_origin


def _tidy_reading(reading, switches: dict | None = None, origin: str = ""):
    if not isinstance(reading, dict):
        return reading
    stem, options, _kind, _origin = tidy_text(
        reading.get("stem") if isinstance(reading.get("stem"), str) else "",
        reading.get("options") if isinstance(reading.get("options"), dict) else {},
        "unknown",
        origin,
        switches=switches,
    )
    changed = dict(reading)
    if isinstance(reading.get("stem"), str):
        changed["stem"] = stem
    if isinstance(reading.get("options"), dict):
        changed["options"] = options
    return changed


OBSOLETE_SECOND_READ_FLAG_PREFIX = "只有一次识读成功（另一次："
OBSOLETE_NUMBER_SEEN_PREFIX = "AI 看到的题号是 "
OBSOLETE_EXTRA_NUMBER_PREFIX = "这张卡的范围里还印着第 "
OBSOLETE_EXTRA_NUMBER_MARKER = "题的题号，卡上只有这一道；请对照原书确认那几道题是不是漏进来了"
OBSOLETE_REEXTRACT_PREFIX = "按章节结构重新提取，"
OBSOLETE_REEXTRACT_MARKER = "原审核记录保留在旧版本中"
OBSOLETE_CANDIDATE_TEXT_PREFIX = "文字是本次候选，"


def is_obsolete_second_read_flag(flag: object) -> bool:
    """判断是否为已废弃的二次复核模型警告旗标。"""
    return str(flag or "").startswith(OBSOLETE_SECOND_READ_FLAG_PREFIX)


def is_obsolete_number_warning_flag(flag: object) -> bool:
    """判断是否为已取消的题号核对或范围内印着其他题号提示。"""
    text = str(flag or "")
    if text.startswith(OBSOLETE_NUMBER_SEEN_PREFIX) and text.endswith("，请确认"):
        return True
    return text.startswith(OBSOLETE_EXTRA_NUMBER_PREFIX) and OBSOLETE_EXTRA_NUMBER_MARKER in text


def is_obsolete_reextract_flag(flag: object) -> bool:
    """判断是否为旧版按章节结构重提取与候选文字提示条。"""
    text = str(flag or "")
    if text.startswith(OBSOLETE_REEXTRACT_PREFIX) and OBSOLETE_REEXTRACT_MARKER in text:
        return True
    return text.startswith(OBSOLETE_CANDIDATE_TEXT_PREFIX)


def is_obsolete_review_flag(flag: object) -> bool:
    """判断是否为版本升级后不再展示、应自动清理的历史提示旗标。"""
    return (
        is_obsolete_second_read_flag(flag) or is_obsolete_number_warning_flag(flag) or is_obsolete_reextract_flag(flag)
    )


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

    from . import cut_guard
    from .pipeline import read_c_with_spots

    switches = features.load()
    counts = {"questions": 0, "publications": 0}
    with transaction.atomic():
        for question in Question.all_objects.select_for_update().select_related("paper", "group"):
            raw_flags = list(question.flags or [])
            clear_candidate_source = cut_guard.is_unconfirmed_candidate_source_card(question)
            edited_value = False if clear_candidate_source else bool(question.edited)
            text_source_value = "" if clear_candidate_source else str(question.text_source or "")
            if clear_candidate_source:
                stem, options = "", {}
                kind = question.question_type
                origin = question.origin
            else:
                stem, options, kind, origin = tidy_text(
                    question.stem, question.options, question.question_type, question.origin, switches=switches
                )
            if not (question.approved or edited_value or question.type_locked):
                # 1.10.1: a choice card under “…有多项符合题目要求…” is multiple
                # choice, whatever the reader said.  Only cards nobody has
                # approved, edited or typed by hand.
                kind = qtypes.with_section(kind, question.section)
            invented: list[str] = []
            if not (question.approved or edited_value or question.body_mode == "source_image"):
                # 1.10.2: an option the model wrote as “（原卷此处为配图，无印刷文字）”
                # was not read; say so instead of keeping the note as its text.
                figure_slots = {figure.get("slot") for figure in question.figures or [] if isinstance(figure, dict)}
                options, invented = _without_invented_options(options, figure_slots)
            answer = prose.tidy_value(question.answer, switches=switches)
            analysis = prose.tidy_value(question.analysis, switches=switches)
            current_flags = [flag for flag in raw_flags if not is_obsolete_review_flag(flag)]
            removed_obsolete = len(current_flags) != len(raw_flags)
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
            if (
                removed_obsolete
                and not current_flags
                and base_state == Question.State.YELLOW
                and review.get("status") not in {"blocked_missing", "conflict"}
                and not question.error
                and question.body_mode != "source_image"
            ):
                base_state = Question.State.GREEN
            flags, state = qtypes.sync(current_flags, base_state, kind)
            if invented and state == Question.State.GREEN:
                state = Question.State.YELLOW
            before = (
                question.stem,
                dict(question.options or {}),
                question.question_type,
                question.origin,
                question.answer,
                question.analysis,
                list(question.flags or []),
                question.state,
                bool(question.edited),
                str(question.text_source or ""),
            )
            if (stem, options, kind, origin, answer, analysis, flags, state, edited_value, text_source_value) == before:
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
            question.edited, question.text_source = edited_value, text_source_value
            spotted = read_c_with_spots(question)
            if spotted is not None:
                question.read_c = spotted
            for field in ("read_a", "read_b", "read_c"):
                setattr(question, field, _tidy_reading(getattr(question, field), switches, origin))
            if approval_was_current and approval_is_current_ignoring_hash(question):
                question.approved_content_hash = approval_hash(question)
            question.save(
                update_fields=[
                    "stem",
                    "options",
                    "question_type",
                    "origin",
                    "answer",
                    "analysis",
                    "flags",
                    "state",
                    "edited",
                    "text_source",
                    "read_a",
                    "read_b",
                    "read_c",
                    "approved_content_hash",
                    "updated_at",
                ]
            )
            counts["questions"] += 1

        for publication in PublishedQuestion.objects.select_for_update():
            content = deepcopy(publication.content)
            original = (
                content.get("stem") or "",
                dict(content.get("options") or {}),
                content.get("question_type") or "unknown",
                str(content.get("origin") or ""),
                content.get("answer") or "",
                content.get("analysis") or "",
            )
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
    if blocks_approval(source_images.review(question)) or type_blocks_approval(question):
        return False
    return bool(question.approved and question.state in REVIEWABLE_STATES and source_images.body_valid(question))


# 1.5.1 name for the same cleanup.
strip_saved_example_labels = tidy_saved_cards

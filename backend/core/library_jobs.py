"""题库里的两件可选的事：补知识点标签、做 AI 参考答案（设置里默认都关）。

网页只排队（网页进程拿不到密钥），后台工作者用读题服务做完再写回。
结果存在题库条目的 extras 里：不属于题面快照，不出新版本、不用重审；
AI 答案和原卷答案分开放，题库和打印里都标着“AI 参考 · 未核对”。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone
from PIL import Image

from . import features, imaging, knowledge, library, qtypes, readers
from .models import LibraryJob, PublishedQuestion

logger = logging.getLogger("core")

FEATURE_OF = {LibraryJob.Kind.TAGS: "knowledge_tags", LibraryJob.Kind.ANSWER: "ai_answer"}
ACTIVE = (LibraryJob.Status.QUEUED, LibraryJob.Status.RUNNING)
NO_ENGINE = "没有可用的读题服务：请先在“设置 → 常用”里填魔搭、MiniMax 或硅基流动的密钥"


class JobError(ValueError):
    pass


_ANSWER_TAG = re.compile(r"【\s*(答案|解析|知识点)\s*】")


def split_answer_tags(raw: str) -> dict[str, str]:
    """【答案】…【解析】…【知识点】… from a model's reply (thinking blocks dropped)."""
    text = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S).strip()
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    parts = _ANSWER_TAG.split(text)
    result: dict[str, str] = {}
    for index in range(1, len(parts) - 1, 2):
        value = re.sub(r"^[：:]\s*", "", parts[index + 1].strip()).strip()
        result.setdefault(parts[index], value)
    return result


def enqueue(publication: PublishedQuestion, kind: str) -> LibraryJob:
    """Queue one job; an identical job already waiting is reused."""
    if kind not in FEATURE_OF:
        raise JobError("不认识的任务")
    if not features.enabled(FEATURE_OF[kind]):
        raise JobError("这个功能在设置的“功能开关”里关着，打开后再用")
    if publication.status != PublishedQuestion.Status.PUBLISHED:
        raise JobError("这道题已不在正式题库里")
    existing = publication.jobs.filter(kind=kind, status__in=ACTIVE).first()
    if existing is not None:
        return existing
    return LibraryJob.objects.create(publication=publication, kind=kind)


def pending_kinds(publication_ids) -> dict[str, list[str]]:
    """{publication id: [kinds waiting or running]} for the library list."""
    result: dict[str, list[str]] = {}
    rows = LibraryJob.objects.filter(publication_id__in=list(publication_ids), status__in=ACTIVE)
    for row in rows.values("publication_id", "kind"):
        result.setdefault(str(row["publication_id"]), []).append(row["kind"])
    return result


def last_errors(publication_ids) -> dict[str, dict[str, str]]:
    """{publication id: {kind: message}} for the most recent failed job of each kind."""
    result: dict[str, dict[str, str]] = {}
    rows = LibraryJob.objects.filter(publication_id__in=list(publication_ids)).order_by("created_at")
    for row in rows.values("publication_id", "kind", "status", "error"):
        entry = result.setdefault(str(row["publication_id"]), {})
        if row["status"] == LibraryJob.Status.FAILED:
            entry[row["kind"]] = row["error"]
        else:
            entry.pop(row["kind"], None)
    return {key: value for key, value in result.items() if value}


def pending() -> bool:
    return LibraryJob.objects.filter(status=LibraryJob.Status.QUEUED).exists()


# ---------------------------------------------------------------- 提示词

def _question_text(content: dict) -> str:
    lines = [f"题型：{qtypes.label(content.get('question_type'))}", "题面：", str(content.get("stem") or "")]
    options = content.get("options") or {}
    for key in library.OPTION_KEYS:
        value = str(options.get(key) or "").strip()
        if value:
            lines.append(f"{key}. {value}")
    return "\n".join(lines)


def answer_prompt(content: dict, with_figures: bool) -> str:
    return "\n".join([
        "你是中学数学老师。请认真解下面这道题，给出最终答案和简要解析。",
        "题面里的公式用 LaTeX，$...$ 包住。" + ("题目的配图按顺序附在后面。" if with_figures else ""),
        "要求：",
        "- 选择题的【答案】只写选项字母（多选写全，如 ACD）；填空题写要填的内容；判断题写“对”或“错”；"
        "解答题分小问写结论，如“(1) 3≤m≤4；(2) 3≤m≤9/2”。",
        "- 【解析】写关键步骤，不超过 300 字，公式用 $...$。",
        "- 条件不够、题目看不懂或缺图时，【答案】写“无法确定”，在【解析】里说明原因，不要硬猜。",
        "只按下面的格式输出，不要输出别的内容：",
        "【答案】…",
        "【解析】…",
        "",
        _question_text(content),
    ])


def tags_prompt(content: dict, points: list[dict]) -> str:
    catalogue = "\n".join(f"- {item['point']}" for item in points)
    return "\n".join([
        "下面是一道数学题。请从“知识点目录”里选出这道题主要考查的 1 到 3 个知识点，最重要的写在前面。",
        "只能照抄目录里的原词，不要改写，不要自己造词；目录里没有合适的就写“无”。",
        "只按下面的格式输出，不要输出别的内容：",
        "【知识点】知识点一；知识点二",
        "",
        "知识点目录：",
        catalogue,
        "",
        _question_text(content),
    ])


def _figure_urls(publication: PublishedQuestion) -> list[str]:
    urls = []
    folder = Path(settings.DATA_ROOT) / "library" / str(publication.id)
    for figure in (publication.content or {}).get("figures") or []:
        name = str(figure.get("file") or "")
        target = folder / name
        if not name or not target.is_file():
            continue
        with Image.open(target) as image:
            urls.append(imaging.jpeg_data_url(image, long_side=1400))
    return urls[:6]


# ---------------------------------------------------------------- 后台执行

def _engine():
    engine = readers.primary_engine()
    if engine is None:
        raise JobError(NO_ENGINE)
    return engine


def run_answer(publication: PublishedQuestion) -> dict:
    content = publication.content or {}
    figures = _figure_urls(publication)
    engine = _engine()
    raw = readers.chat(engine, answer_prompt(content, bool(figures)), figures, max_tokens=2500)
    tags = split_answer_tags(raw)
    answer = str(tags.get("答案") or "").strip()
    if not answer:
        raise JobError("模型没有按格式给出答案，请稍后再试")
    return {
        "answer": answer[:2000],
        "analysis": str(tags.get("解析") or "").strip()[:6000],
        "engine": readers.answered_by(engine).label,
        "at": timezone.now().isoformat(),
    }


def run_tags(publication: PublishedQuestion) -> tuple[list[str], str]:
    points = knowledge.load()
    if not points:
        raise JobError("知识点目录是空的，请检查数据目录里的 knowledge-points.txt")
    engine = _engine()
    raw = readers.chat(engine, tags_prompt(publication.content or {}, points), [], max_tokens=300)
    named = split_answer_tags(raw).get("知识点", raw)
    return knowledge.match_tags(named, points), readers.answered_by(engine).label


def _finish(job: LibraryJob, status: str, error: str = "") -> None:
    job.status = status
    job.error = error[:300]
    job.save(update_fields=["status", "error", "updated_at"])


def process_pending(limit: int = 20) -> int:
    """Run queued jobs (worker only).  Returns how many were handled."""
    handled = 0
    for _ in range(limit):
        close_old_connections()
        with transaction.atomic():
            job = LibraryJob.objects.select_for_update().filter(status=LibraryJob.Status.QUEUED) \
                .select_related("publication").order_by("created_at").first()
            if job is None:
                break
            job.status = LibraryJob.Status.RUNNING
            job.save(update_fields=["status", "updated_at"])
        handled += 1
        publication = job.publication
        try:
            if not features.enabled(FEATURE_OF[job.kind]):
                raise JobError("这个功能已在设置里关掉")
            if job.kind == LibraryJob.Kind.ANSWER:
                result = run_answer(publication)
                with transaction.atomic():
                    publication = PublishedQuestion.objects.select_for_update().get(pk=publication.pk)
                    library.save_extras(publication, {**(publication.extras or {}), "ai_answer": result})
            else:
                tags, engine = run_tags(publication)
                with transaction.atomic():
                    publication = PublishedQuestion.objects.select_for_update().get(pk=publication.pk)
                    library.save_extras(publication, {**(publication.extras or {}), "tags": tags,
                                                      "tags_source": engine, "tags_at": timezone.now().isoformat()})
            _finish(job, LibraryJob.Status.DONE)
        except (JobError, readers.ReaderError) as error:
            _finish(job, LibraryJob.Status.FAILED, str(error) or "失败了，请稍后再试")
        except Exception as error:  # 一道题失败不影响别的题，也不让工作者退出
            logger.exception("library job failed")
            _finish(job, LibraryJob.Status.FAILED, f"出错了：{str(error) or type(error).__name__}")
    return handled


def recover_interrupted() -> int:
    """Jobs left running by a worker that stopped go back to the queue."""
    return LibraryJob.objects.filter(status=LibraryJob.Status.RUNNING).update(status=LibraryJob.Status.QUEUED)

"""题库里的两件可选的事：补知识点标签、做 AI 参考答案（设置里默认都关）。

网页只排队，默认由当前助手通过本地工具写回；后台只处理显式选择的 API 任务。
结果存在题库条目的 extras 里：不属于题面快照，不出新版本、不用重审；
AI 答案和原卷答案分开放，题库和打印里都标着“AI 参考 · 未核对”。
"""

from __future__ import annotations

import logging
import io
import re
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone
from PIL import Image

from . import features, imaging, knowledge, library, library_ai_settings, qtypes
from .models import LibraryJob, PublishedQuestion, Question

logger = logging.getLogger("core")

FEATURE_OF = {LibraryJob.Kind.TAGS: "knowledge_tags", LibraryJob.Kind.ANSWER: "ai_answer"}
ACTIVE = (LibraryJob.Status.QUEUED, LibraryJob.Status.RUNNING)


class JobError(ValueError):
    pass


def _no_existing_result(publication, kind):
    extras = publication.extras or {}
    if kind == "answer" and isinstance(extras.get("ai_answer"), dict) and extras["ai_answer"].get("answer"):
        raise JobError("这个入库版已有 AI 参考答案，未重复覆盖。")
    if kind == "tags" and library.tags_of(extras):
        raise JobError("这个入库版已有知识点标签，未重复覆盖。")


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


def enqueue(publication: PublishedQuestion, kind: str, *, agent: str = "", solution_scope: bool = False) -> LibraryJob:
    """Queue one job; an identical job already waiting is reused."""
    from .library_job_control import expire_api_jobs
    expire_api_jobs()
    if kind not in FEATURE_OF:
        raise JobError("不认识的任务")
    if solution_scope and kind != LibraryJob.Kind.ANSWER:
        raise JobError("本次组卷建议仅支持答案解析")
    if not solution_scope and not features.enabled(FEATURE_OF[kind]):
        raise JobError("这个功能在“标签与参考答案设置”里关着，打开后再用")
    if publication.status != PublishedQuestion.Status.PUBLISHED:
        raise JobError("这道题已不在正式题库里")
    try:
        state = library_ai_settings.ensure_ready(None if solution_scope else kind)
    except library_ai_settings.SettingsError as error:
        raise JobError(str(error)) from None
    executor = state.get("mode", "assistant")
    with transaction.atomic():
        if publication.question_id:
            Question.all_objects.select_for_update().filter(pk=publication.question_id).first()
        publication = PublishedQuestion.objects.select_for_update().get(pk=publication.pk)
        if live_version(publication) is None:
            raise JobError("这道题已撤回或被新版替代，请使用当前入库版。")
        if not solution_scope:
            _no_existing_result(publication, kind)
        fingerprint = library.generation_fingerprint(publication.content or {}, publication.pk)
        existing = publication.jobs.filter(kind=kind, executor=executor, fingerprint=fingerprint, solution_scope=solution_scope, status__in=ACTIVE).first()
        if existing is not None:
            return existing
        return LibraryJob.objects.create(publication=publication, kind=kind, executor=executor,
                                         fingerprint=fingerprint, agent=agent,
                                         solution_scope=solution_scope,
                                         api_snapshot=library_ai_settings.execution_snapshot() if executor == "api" else {})


def pending_kinds(publication_ids) -> dict[str, list[str]]:
    """{publication id: [kinds waiting or running]} for the library list."""
    result: dict[str, list[str]] = {}
    rows = LibraryJob.objects.filter(publication_id__in=list(publication_ids), status__in=ACTIVE)
    for row in rows.values("publication_id", "kind"):
        result.setdefault(str(row["publication_id"]), []).append(row["kind"])
    return result


def queue_on_intake(publication: PublishedQuestion) -> list[LibraryJob]:
    """Only enqueue after a new publication; optional service absence cannot undo it."""
    state = library_ai_settings.public_status()
    jobs = []
    for kind, feature in FEATURE_OF.items():
        if not state.get("on_intake", {}).get(kind) or not features.enabled(feature):
            continue
        if kind == "tags" and library.tags_of(publication.extras):
            continue
        if kind == "answer" and (str((publication.content or {}).get("answer") or "").strip()
                                 or (publication.extras or {}).get("ai_answer")):
            continue
        try:
            jobs.append(enqueue(publication, kind))
        except (JobError, library_ai_settings.SettingsError) as error:
            jobs.append(LibraryJob.objects.create(
                publication=publication, kind=kind, executor=state["mode"],
                fingerprint=library.generation_fingerprint(publication.content or {}, publication.pk),
                api_snapshot=library_ai_settings.execution_snapshot() if state["mode"] == "api" else {},
                status=LibraryJob.Status.FAILED, error=str(error)[:300]))
    return jobs


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
    return LibraryJob.objects.filter(executor=LibraryJob.Executor.API, status=LibraryJob.Status.QUEUED).exists()


# ---------------------------------------------------------------- 提示词

def _question_text(content: dict) -> str:
    if content.get("body_mode") == "source_image":
        return "题型：" + qtypes.label(content.get("question_type")) + "\n题面为按阅读顺序附上的原卷截图，必须看清全部片段；不能从空文字猜题。"
    lines = [f"题型：{qtypes.label(content.get('question_type'))}", "题面：", str(content.get("stem") or "")]
    options = content.get("options") or {}
    for key in library.OPTION_KEYS:
        value = str(options.get(key) or "").strip()
        if value:
            lines.append(f"{key}. {value}")
    return "\n".join(lines)


def answer_prompt(content: dict, with_figures: bool, *, detailed=False) -> str:
    return "\n".join([
        "你是中学数学老师。请认真解下面这道题，给出最终答案和简要解析。",
        "题面里的公式用 LaTeX，$...$ 包住。" + ("题目的配图按顺序附在后面。" if with_figures else ""),
        "要求：",
        "- 选择题的【答案】只写选项字母（多选写全，如 ACD）；填空题写要填的内容；判断题写“对”或“错”；"
        "解答题分小问写结论，如“(1) 3≤m≤4；(2) 3≤m≤9/2”。",
        "- 【解析】逐小问写完整推导和理由，空行分段，公式用 $...$；不只给结论。总长度不超过6000字。" if detailed else
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
    if (publication.content or {}).get("body_mode") == "source_image":
        # Use the same immutable PNG/hash checks as export. Do not give the
        # model an empty stem while silently dropping the real question body.
        from . import library_export
        try:
            images = library_export._images(publication, "原卷图片题")
        except library_export.ExportError as error:
            raise JobError(str(error)) from None
        if len(images) > 6:
            raise JobError("原图题超过 6 个片段，当前独立模型接口无法完整接收；请使用当前助手或先补齐文字。")
        for item in images:
            with Image.open(io.BytesIO(item["bytes"])) as image:
                urls.append(imaging.jpeg_data_url(image, long_side=2400))
        return urls
    folder = Path(settings.DATA_ROOT) / "library" / str(publication.id)
    for figure in (publication.content or {}).get("figures") or []:
        name = str(figure.get("file") or "")
        if not name or Path(name).name != name or "/" in name or "\\" in name:
            raise JobError("题目配图文件无效，未生成答案或标签；请先核对配图。")
        target = folder / name
        if not target.is_file():
            raise JobError("题目配图缺失，未生成答案或标签；请先核对配图。")
        try:
            with Image.open(target) as image:
                urls.append(imaging.jpeg_data_url(image, long_side=1400))
        except (OSError, ValueError):
            raise JobError("题目配图无法打开，未生成答案或标签；请先核对配图。") from None
    if len(urls) > 6:
        raise JobError("这道题配图超过 6 张，未忽略配图生成；请人工核对。")
    return urls


# ---------------------------------------------------------------- 后台执行

def run_answer(publication: PublishedQuestion, *, explicit_once=False) -> dict:
    content = publication.content or {}
    figures = _figure_urls(publication)
    raw, engine = library_ai_settings.chat(answer_prompt(content, bool(figures), detailed=explicit_once), figures, kind=None if explicit_once else "answer")
    tags = split_answer_tags(raw)
    answer = str(tags.get("答案") or "").strip()
    if not answer:
        raise JobError("模型没有按格式给出答案，请稍后再试")
    return {
        "answer": answer[:2000],
        "analysis": str(tags.get("解析") or "").strip()[:6000],
        "engine": engine,
        "at": timezone.now().isoformat(),
        "publication_id": str(publication.id),
        "fingerprint": library.generation_fingerprint(content, publication.id),
        "checked": False,
        "executor": "api",
    }


def run_tags(publication: PublishedQuestion) -> tuple[list[str], str]:
    points = knowledge.load()
    if not points:
        raise JobError("知识点目录是空的，请检查数据目录里的 knowledge-points.txt")
    figures = _figure_urls(publication)
    raw, engine = library_ai_settings.chat(tags_prompt(publication.content or {}, points), figures, kind="tags")
    named = split_answer_tags(raw).get("知识点", raw)
    return knowledge.match_tags(named, points), engine


def live_version(publication: PublishedQuestion) -> PublishedQuestion | None:
    """An old job never silently changes its target to a replacement version."""
    current = PublishedQuestion.objects.filter(pk=publication.pk, status=PublishedQuestion.Status.PUBLISHED).first()
    if current is None:
        return None
    if current.question_id and PublishedQuestion.objects.filter(
            question_id=current.question_id, status=PublishedQuestion.Status.PUBLISHED,
            version__gt=current.version).exists():
        return None
    return current


def _bound_target(job: LibraryJob, publication: PublishedQuestion, fingerprint: str, content_hash: str) -> PublishedQuestion:
    """Called inside the write transaction after a potentially long model call."""
    from .library_job_control import expire_api_jobs
    expire_api_jobs()
    if publication.question_id:
        Question.all_objects.select_for_update().filter(pk=publication.question_id).first()
    current_job = LibraryJob.objects.select_for_update().get(pk=job.pk)
    current = PublishedQuestion.objects.select_for_update().get(pk=publication.pk)
    if (current_job.status != LibraryJob.Status.RUNNING or current_job.executor != LibraryJob.Executor.API
            or current_job.publication_id != publication.pk or current_job.fingerprint != fingerprint
            or live_version(current) is None or current.content_hash != content_hash
            or library.generation_fingerprint(current.content or {}, current.pk) != fingerprint):
        raise JobError("生成期间题面、配图或入库版本已变化，旧结果未保存；请在当前版本重新生成。")
    if not job.solution_scope and not features.enabled(FEATURE_OF[job.kind]):
        raise JobError("生成期间这个功能已关闭，结果未保存。")
    library_ai_settings.ensure_ready(None if job.solution_scope else job.kind)
    library_ai_settings.require_snapshot(current_job.api_snapshot)
    if not job.solution_scope:
        _no_existing_result(current, job.kind)
    return current


def _finish(job: LibraryJob, status: str, error: str = "") -> None:
    # Cancellation/expiry owns its terminal state, including while a request
    # is outside this transaction. A late success or failure cannot replace it.
    changed = LibraryJob.objects.filter(pk=job.pk, status=LibraryJob.Status.RUNNING).update(
        status=status, error=error[:300], updated_at=timezone.now())
    if changed:
        job.status, job.error = status, error[:300]


def process_pending(limit: int = 5) -> int:
    """Run queued jobs (worker only).  Returns how many were handled."""
    from .library_job_control import expire_api_jobs
    expire_api_jobs()
    handled = 0
    for _ in range(limit):
        close_old_connections()
        with transaction.atomic():
            job = LibraryJob.objects.select_for_update().filter(executor=LibraryJob.Executor.API, status=LibraryJob.Status.QUEUED) \
                .select_related("publication").order_by("created_at").first()
            if job is None:
                break
            job.status = LibraryJob.Status.RUNNING
            job.save(update_fields=["status", "updated_at"])
        handled += 1
        try:
            publication = live_version(job.publication)
            if publication is None:
                raise JobError("这道题已撤回或被新版替代，旧任务未执行；请在当前版本重新生成。")
            if not job.solution_scope and not features.enabled(FEATURE_OF[job.kind]):
                raise JobError("这个功能已在设置里关掉")
            if not job.solution_scope:
                _no_existing_result(publication, job.kind)
            fingerprint = library.generation_fingerprint(publication.content or {}, publication.pk)
            if not job.fingerprint or job.fingerprint != fingerprint:
                raise JobError("排队后题面或配图已变化，旧任务未执行；请在当前版本重新排队。")
            library_ai_settings.require_snapshot(job.api_snapshot)
            initial_hash = publication.content_hash
            if job.kind == LibraryJob.Kind.ANSWER:
                result = run_answer(publication, explicit_once=True) if job.solution_scope else run_answer(publication)
                with transaction.atomic():
                    publication = _bound_target(job, publication, fingerprint, initial_hash)
                    result.update(fingerprint=fingerprint, publication_id=str(publication.pk), checked=False)
                    if job.solution_scope:
                        job.result = result
                        job.save(update_fields=["result", "updated_at"])
                    else:
                        library.save_extras(publication, {**(publication.extras or {}), "ai_answer": result})
                    _finish(job, LibraryJob.Status.DONE)
            else:
                tags, engine = run_tags(publication)
                with transaction.atomic():
                    publication = _bound_target(job, publication, fingerprint, initial_hash)
                    library.save_extras(publication, {**(publication.extras or {}), "tags": tags,
                                                      "tags_source": engine, "tags_at": timezone.now().isoformat(),
                                                      "tags_executor": "api", "tags_checked": False,
                                                      "tags_fingerprint": fingerprint, "tags_publication_id": str(publication.pk)})
                    _finish(job, LibraryJob.Status.DONE)
        except (JobError, library_ai_settings.SettingsError) as error:
            expire_api_jobs()
            _finish(job, LibraryJob.Status.FAILED, str(error) or "失败了，请稍后再试")
        except Exception as error:  # 一道题失败不影响别的题，也不让工作者退出
            expire_api_jobs()
            logger.error("library job failed (%s)", type(error).__name__)
            _finish(job, LibraryJob.Status.FAILED, "本机生成任务未完成，结果未保存；请稍后重试。")
    return handled


def recover_interrupted() -> int:
    """Jobs left running by a worker that stopped go back to the queue."""
    from .library_job_control import expire_api_jobs
    expire_api_jobs()
    return LibraryJob.objects.filter(executor=LibraryJob.Executor.API, status=LibraryJob.Status.RUNNING).update(status=LibraryJob.Status.QUEUED)

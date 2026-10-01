"""框选识读：在原卷上框出一小块，让读题模型单独读这一块（1.10.2）。

起因：高一质量检测一第 11 题的 A 选项被手写的 × 盖住，整题识读时裁决把它写成
了“（原卷此处为配图，无印刷文字）”。整题重读只会再看一遍同一张图；把印刷的
那一行单独框出来、放大了读，干扰就少得多。

网页只排队（网页进程拿不到密钥），后台工作者读完写回。读出来的文字不直接改
题卡：由人看过后点“填入”，经“改字”保存，和人工改字一样要重新审核。
"""

from __future__ import annotations

import logging
import re

from django.db import close_old_connections, transaction
from django.utils import timezone
from PIL import Image

from . import imaging, readers, textnorm
from .models import RegionRead

logger = logging.getLogger("core")

TARGETS = ("stem", "A", "B", "C", "D", "E")
TARGET_NAMES = {"stem": "题干", **{key: f"选项 {key}" for key in "ABCDE"}}
NO_ENGINE = "框选识读要用看图读题的服务：请先在“设置 → 常用”里填魔搭、MiniMax 或硅基流动的密钥"
PAD = 6.0             # 框外多留一点（页面单位），免得切到笔画
MIN_LONG_SIDE = 1100  # 小块放大到这么大再给模型看
ACTIVE = (RegionRead.Status.QUEUED, RegionRead.Status.RUNNING)


class RegionError(ValueError):
    pass


def prompt(target: str) -> str:
    if target == "stem":
        rule = "这一块是题干的一部分：照原样誊录，题号不要写。"
    else:
        rule = f"这一块是选择题的选项 {target}：选项字母“{target}.”本身不要写，只写它后面的内容。"
    return "\n".join([
        "图片是从一张数学试卷上框出的一小块。请只誊录其中印刷的内容：",
        "- 学生手写的字、勾、叉、圈画和批改符号一律忽略；印刷的字上画了 × 或圈，照样誊录印刷的字。",
        "- 数学式用 LaTeX，行内公式用 $...$ 包住；中文和中文标点照原卷。",
        "- 看不清的字写成 [?]，不要猜，也不要补全框外的内容。",
        f"- {rule}",
        "只输出誊录的文字，不要解释，不要加【】标签、引号或代码块。框里没有印刷文字时只输出：无",
    ])


_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")
_TAG = re.compile(r"^\s*【[^】]{1,8}】\s*")


def clean(raw: str, target: str) -> str:
    """The transcription from a model's reply, without its wrapping."""
    text = _FENCE.sub("", _THINK.sub("", str(raw or "")).strip()).strip()
    text = _TAG.sub("", text)
    if target in "ABCDE":
        # “A. f(1,5)=f(5,1)” / “（A）…”: the letter is the option's label, not its text.
        text = re.sub(rf"^\s*(?:[（(]\s*{target}\s*[)）]\s*[.．、:：]?|{target}\s*[.．、:：])\s*", "", text)
    text = textnorm.fix_symbols(text.strip())
    text, _described = readers.strip_bracketed_figure_descriptions(text)
    if text.strip() in {"", "无", "（无）", "(无)"}:
        raise RegionError("框里没有读到印刷文字，换个位置框一下试试")
    return text.strip()[:4000]


def crop(page: Image.Image, bbox: list[float]) -> Image.Image:
    x0, y0, x1, y1 = bbox
    padded = [max(0.0, x0 - PAD), max(0.0, y0 - PAD), min(1000.0, x1 + PAD), min(1000.0, y1 + PAD)]
    piece = page.crop(imaging.to_pixels(padded, page.size)).convert("RGB")
    long_side = max(piece.size)
    if long_side < MIN_LONG_SIDE:
        scale = MIN_LONG_SIDE / max(1, long_side)
        piece = piece.resize((max(1, round(piece.width * scale)), max(1, round(piece.height * scale))),
                             Image.Resampling.LANCZOS)
    return piece


def run(job: RegionRead) -> tuple[str, str]:
    from .pipeline import PageStore

    engine = readers.primary_engine()
    if engine is None:
        raise RegionError(NO_ENGINE)
    page = PageStore(job.question.paper).load(job.page_idx)
    url = imaging.jpeg_data_url(crop(page, list(job.bbox)), long_side=max(MIN_LONG_SIDE, 1600))
    raw = readers.chat(engine, prompt(job.target), [url], max_tokens=800)
    return clean(raw, job.target), readers.answered_by(engine).label


def pending() -> bool:
    return RegionRead.objects.filter(status=RegionRead.Status.QUEUED).exists()


def _finish(job: RegionRead, status: str, *, text: str = "", error: str = "", engine: str = "") -> None:
    # The person may have closed it or framed a new box meanwhile: then there is nothing to write.
    RegionRead.objects.filter(pk=job.pk, status=RegionRead.Status.RUNNING).update(
        status=status, text=text, error=error[:300], engine=engine[:80], updated_at=timezone.now())


def process_pending(limit: int = 5) -> int:
    """Read queued regions (worker only).  Returns how many were handled.

    The worker's main loop and its reread lane both call this; a job is
    claimed with a conditional update, so only one of them reads it.
    """
    handled = 0
    for _ in range(limit):
        close_old_connections()
        job = RegionRead.objects.filter(status=RegionRead.Status.QUEUED).order_by("created_at").first()
        if job is None:
            break
        with transaction.atomic():
            claimed = RegionRead.objects.filter(pk=job.pk, status=RegionRead.Status.QUEUED) \
                .update(status=RegionRead.Status.RUNNING)
        if not claimed:
            continue
        handled += 1
        try:
            job = RegionRead.objects.select_related("question__paper").get(pk=job.pk)
            text, engine = run(job)
            _finish(job, RegionRead.Status.DONE, text=text, engine=engine)
        except RegionRead.DoesNotExist:
            continue  # the card or the request was removed meanwhile
        except (RegionError, readers.ReaderError) as error:
            _finish(job, RegionRead.Status.FAILED, error=str(error) or "没读出来，请稍后再试")
        except Exception as error:  # 一块失败不影响别的，也不让工作者退出
            logger.exception("region read failed")
            _finish(job, RegionRead.Status.FAILED, error=f"出错了：{str(error) or type(error).__name__}")
    return handled


def recover_interrupted() -> int:
    """Reads left running by a worker that stopped go back to the queue."""
    return RegionRead.objects.filter(status=RegionRead.Status.RUNNING).update(status=RegionRead.Status.QUEUED)


def latest_json(question) -> dict | None:
    """The card's last region read, for the review page (uses a prefetch when there is one)."""
    reads = list(question.region_reads.all())
    if not reads:
        return None
    job = max(reads, key=lambda item: (item.created_at, item.pk))
    return {
        "id": job.pk, "target": job.target, "target_name": TARGET_NAMES.get(job.target, job.target),
        "status": job.status, "text": job.text, "error": job.error, "engine": job.engine,
        "page_idx": job.page_idx, "bbox": job.bbox,
    }

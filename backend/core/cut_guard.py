"""1.26.47：一条规则，前后端共用 —— 这道题还能不能让 AI 识读。

后端对原图题有三条硬保护：已审核通过、**在用的**入库记录、已经有人工或助手
改过的文字。满足任意一条，reread 接口直接 409 —— 不能让人写过的东西被 AI 盖掉。

以前审题页面只在按钮上判断「已审核」和「在用的入库记录」：改过字的题都能点
「AI 识读这题」，点了才弹一条红条说不行。现在后端把判定和原因一起透出，
页面照着做 —— 事前就说清楚为什么灰、下一步去哪。

1.26.51：入库记录按**在不在用**算，不按「有没有过」算。

  老师从题库里「撤回」一道题，意思正是「这道我不入库了，我要回原卷重做」。
  撤回过的记录和被新版替代的记录都不在题库里了，题库里没有它们，AI 重新
  识读这道原卷上的题，既不会盖掉题库里的任何东西，也不会盖掉人工写过的字。

  可老师实际遇到的却是：撤回过的记录照样挡住识读，点一下就弹一条红条说不行。
  本机真库实测：**392 道**原图题就是这么被挡住的 —— 它们的入库记录全都
  撤回了，题库里一条活的都没有（published 计数为 0），入库快照与当前题目
  逐字相同（394/397 条完全一致），也就是说**那里头根本没有人工成果**。

  所以这里改成：只有**在用的**入库记录（status='published'）才挡识读。
  撤回的、被替代的都不挡 —— 它们不在题库里，挡不住任何东西。
"""

from __future__ import annotations

from . import source_images
from .models import PublishedQuestion

# 挡住识读的三条理由。页面按这三种分别写清「为什么灰、下一步去哪」。
APPROVED = "approved"
PUBLISHED = "published"
MANUAL_TEXT = "manual_text"


def _live_publication_ids(questions) -> set[int]:
    """一次查完，返回**在用的**入库记录所属的题目。

    只认 status='published'：撤回的、被新版替代的都不在题库里了，
    不该再挡住这道题的识读（1.26.51）。
    """
    ids = [question.pk for question in questions if question.pk]
    if not ids:
        return set()
    return set(
        PublishedQuestion.objects.filter(question_id__in=ids, status=PublishedQuestion.Status.PUBLISHED).values_list(
            "question_id", flat=True
        )
    )


def protection_reason(question) -> str:
    """空字符串 = 可以识读；否则是挡住它的那一条理由（与 reread 的 409 口径一致）。"""
    if question.approved:
        return APPROVED
    if question.publications.filter(status=PublishedQuestion.Status.PUBLISHED).exists():
        return PUBLISHED
    if question.edited and (question.stem.strip() or question.options):
        return MANUAL_TEXT
    return ""


def is_protected(question) -> bool:
    return bool(protection_reason(question))


def reasons_for(questions) -> dict[int, str]:
    """一次查完所有在用的入库记录，返回每道题挡住识读的原因（空字符串 = 可以识读）。

    一本书几百道题，不能一道题查一次入库记录 —— 这里只查一次。
    """
    rows = list(questions)
    published = _live_publication_ids(rows)
    reasons: dict[int, str] = {}
    for question in rows:
        if question.approved:
            reason = APPROVED
        elif question.pk in published:
            reason = PUBLISHED
        elif question.edited and (question.stem.strip() or question.options):
            reason = MANUAL_TEXT
        else:
            reason = ""
        reasons[question.pk] = reason
    return reasons


def guard_json(question, reason: str | None = None) -> dict:
    """原图题才受这条规则保护；文字题走的是另一套（人工通过）保护，不能混用。"""
    if not source_images.is_image(question):
        return {"blocked": False, "reason": ""}
    value = protection_reason(question) if reason is None else reason
    return {"blocked": bool(value), "reason": value}

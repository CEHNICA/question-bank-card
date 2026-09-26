"""移除被视觉模型写成选项文字的图片说明。

只处理没有人工改字、且主读者明确绑定了自动配图的题卡。原始 read_a/read_b/read_c
保持不变；最终题面变化后撤销旧审批，必须重新对照原卷确认。正式题库快照不可变，
不会在迁移中改写。
"""

import re

from django.db import migrations


OPTION_KEYS = {"A", "B", "C", "D"}
REVIEW_FLAG = "已移除 AI 对配图内容的重复转写，请对照原卷检查配图后重新审核"
TABLE_REVIEW_FLAG = "表格内容已改由原卷配图呈现，请对照原卷核对后重新审核"
UNFOUND_FIGURE_FLAG = "原卷可能有图没有被找到，请点“配图”框出"
FIGURE_DESCRIPTION = re.compile(
    r"^\s*(?:"
    r"\[\s*(?:图|图片|图形|图示|示意图|表|表格)\s*[:：]\s*\S[\s\S]*\]|"
    r"【\s*(?:图|图片|图形|图示|示意图|表|表格)\s*[:：]\s*\S[\s\S]*】|"
    r"[（(]\s*(?:图|图片|图形|图示|示意图|表|表格)\s*[:：]\s*\S[\s\S]*[）)]"
    r")\s*[。.]?\s*$"
)
BRACKETED_FIGURE_DESCRIPTION = re.compile(
    r"(?:"
    r"\[\s*(?:图|图片|图形|图示|示意图|表|表格)\s*[:：][^\r\n]*\]|"
    r"【\s*(?:图|图片|图形|图示|示意图|表|表格)\s*[:：]\s*[^】]+】|"
    r"[（(]\s*(?:图|图片|图形|图示|示意图|表|表格)\s*[:：]\s*[^）)]+[）)]"
    r")"
)
NUMBER_LINE_DESCRIPTION = re.compile(
    r"^\s*(?:一条)?数轴上(?:从左到右)?标有(?:若干个?)?点\s*[，,]?\s*标号依次为\s*"
    r"(?:\$?\s*[−-]?\s*(?:\d+(?:\.\d+)?|\[\?\])\s*\$?\s*[、，,]\s*){2,}"
    r"\$?\s*[−-]?\s*(?:\d+(?:\.\d+)?|\[\?\])\s*\$?\s*[。.]?\s*$"
)
MARKDOWN_TABLE_SEPARATOR = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")


def _strip_bracketed(value):
    cleaned = BRACKETED_FIGURE_DESCRIPTION.sub("", str(value or ""))
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _strip_markdown_tables(value):
    lines = str(value or "").splitlines()
    kept = []
    index = 0
    while index < len(lines):
        if (index + 1 < len(lines) and "|" in lines[index]
                and MARKDOWN_TABLE_SEPARATOR.fullmatch(lines[index + 1])):
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                index += 1
            continue
        kept.append(lines[index])
        index += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _is_figure_description(value):
    text = str(value or "")
    return bool(FIGURE_DESCRIPTION.fullmatch(text) or NUMBER_LINE_DESCRIPTION.fullmatch(text))


def _automatic_figure_slots(question):
    slots = {
        figure.get("slot") for figure in (question.figures or [])
        if isinstance(figure, dict) and figure.get("source") == "auto"
    }
    slots.update(
        role for role in ((question.read_a or {}).get("figures") or {}).values()
        if role == "stem" or role in OPTION_KEYS
    )
    return slots & (OPTION_KEYS | {"stem"})


def forwards(apps, schema_editor):
    Question = apps.get_model("core", "Question")
    for question in Question.objects.filter(edited=False).iterator():
        if question.text_source == "human":
            continue
        slots = _automatic_figure_slots(question)
        if not slots:
            continue
        options = dict(question.options or {})
        primary_options = (question.read_a or {}).get("options") or {}
        option_slots = slots & OPTION_KEYS
        pure_figure_choice = bool(option_slots) and not any(
            str(primary_options.get(slot, "")).strip() for slot in OPTION_KEYS
        )
        eligible_slots = OPTION_KEYS if pure_figure_choice else option_slots
        cleaned = dict(options)
        removed_slots = set()
        for slot in eligible_slots:
            value = str(options.get(slot, ""))
            if value and _is_figure_description(value):
                cleaned.pop(slot, None)
                removed_slots.add(slot)
            elif value:
                normalized = _strip_bracketed(value)
                if normalized:
                    cleaned[slot] = normalized
                else:
                    cleaned.pop(slot, None)
                    removed_slots.add(slot)
        stem = question.stem
        if "stem" in slots:
            stem = _strip_markdown_tables(_strip_bracketed(stem))
        options_changed = cleaned != options
        stem_changed = stem != question.stem
        if not options_changed and not stem_changed:
            continue

        question.stem = stem
        question.options = cleaned
        question.approved = False
        question.approved_at = None
        question.approved_content_hash = ""
        flags = list(question.flags or [])
        if "[?]" not in question.stem and not any("[?]" in str(value) for value in cleaned.values()):
            flags = [flag for flag in flags if "看不清的字" not in flag]
        if options_changed and REVIEW_FLAG not in flags:
            flags.append(REVIEW_FLAG)
        if stem_changed and TABLE_REVIEW_FLAG not in flags:
            flags.append(TABLE_REVIEW_FLAG)
        if removed_slots - option_slots and UNFOUND_FIGURE_FLAG not in flags:
            flags.append(UNFOUND_FIGURE_FLAG)
        question.flags = flags
        if question.state in {"green", "yellow"}:
            question.state = "yellow"
        question.save(update_fields=[
            "stem", "options", "approved", "approved_at", "approved_content_hash", "flags", "state",
        ])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_normalize_parallelogram_readings"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

"""把终审结果绑定到题面、配图和原卷来源的精确版本。"""

import hashlib
import json

from django.db import migrations, models


OPTION_KEYS = ("A", "B", "C", "D")
CHOICE_TYPES = {"single_choice", "multiple_choice"}
REVIEWABLE_STATES = {"green", "yellow"}


def _approval_hash(question) -> str:
    options = question.options or {}
    is_choice = question.question_type in CHOICE_TYPES or bool(options)
    source_origin = "manual" if question.start_source == "manual" or question.regions != question.regions_auto \
        else question.start_source
    material = {
        "number": question.number,
        "section": question.section,
        "question_type": question.question_type,
        "stem": question.stem,
        "options": {key: options.get(key, "") for key in OPTION_KEYS} if is_choice else {},
        "answer": question.answer or "",
        "analysis": question.analysis or "",
        "document_id": str(question.paper_id),
        "source_filename": question.paper.filename,
        "figures": [
            {key: figure.get(key) for key in ("slot", "page_idx", "bbox", "source")}
            for figure in (question.figures or [])
        ],
        "sources": [
            {"page_idx": region["page_idx"], "bbox": region["bbox"], "type": "text", "source": source_origin}
            for region in (question.regions or [])
        ],
    }
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def bind_existing_approvals(apps, schema_editor):
    Question = apps.get_model("core", "Question")
    for question in Question.objects.select_related("paper").filter(approved=True).iterator():
        if question.state not in REVIEWABLE_STATES or not question.stem.strip():
            question.approved = False
            question.approved_at = None
            question.approved_content_hash = ""
            question.save(update_fields=["approved", "approved_at", "approved_content_hash"])
            continue
        question.approved_content_hash = _approval_hash(question)
        question.save(update_fields=["approved_content_hash"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_fix_parallelogram_symbol"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="approved_content_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.RunPython(bind_existing_approvals, migrations.RunPython.noop),
    ]

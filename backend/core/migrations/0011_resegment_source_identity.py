import re

from django.db import migrations, models


EXAMPLE_RE = re.compile(r"(?:^|\s)(?:例题?|example|ex\.?)\s*\d+", re.I)
EXERCISE_RE = re.compile(r"(?:练习|习题|exercise)", re.I)


def _source_kind(question):
    if question.start_source == "manual":
        return "manual"
    group_kind = getattr(question.group, "kind", "") if question.group_id else ""
    if group_kind == "example":
        return "example"
    if group_kind == "exercise":
        return "exercise"
    text = str(question.section or "")
    if EXAMPLE_RE.search(text):
        return "example"
    if EXERCISE_RE.search(text):
        return "exercise"
    return "unknown"


def _anchor_for(question, blocks_by_page):
    if question.start_source == "manual" or not question.regions:
        return None
    first = question.regions[0] if isinstance(question.regions[0], dict) else None
    if not first or type(first.get("page_idx")) is not int:
        return None
    bbox = first.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        top, bottom = float(bbox[1]), float(bbox[3])
    except (TypeError, ValueError):
        return None
    number = int(question.number)
    number_re = re.compile(rf"(?<!\d){number}\s*[.．、:]?")
    candidates = []
    for block in blocks_by_page.get(first["page_idx"], []):
        block_bbox = block.bbox
        if not isinstance(block_bbox, list) or len(block_bbox) != 4:
            continue
        try:
            y = float(block_bbox[1])
            center_y = (float(block_bbox[1]) + float(block_bbox[3])) / 2
        except (TypeError, ValueError):
            continue
        if center_y < top - 25 or center_y > bottom:
            continue
        text = str(block.text or "")
        if not number_re.search(text):
            continue
        candidates.append((abs(y - (top + 9)), block.seq))
    return min(candidates)[1] if candidates else None


def backfill_source_identity(apps, schema_editor):
    Question = apps.get_model("core", "Question")
    Block = apps.get_model("core", "Block")
    paper_ids = Question.all_objects.order_by().values_list("paper_id", flat=True).distinct()
    for paper_id in paper_ids.iterator():
        blocks_by_page = {}
        for block in Block.objects.filter(paper_id=paper_id).order_by("seq"):
            blocks_by_page.setdefault(block.page_idx, []).append(block)
        pending = []
        questions = Question.all_objects.filter(paper_id=paper_id).select_related("group")
        for question in questions.iterator(chunk_size=500):
            question.source_kind = _source_kind(question)
            question.source_anchor_seq = _anchor_for(question, blocks_by_page)
            pending.append(question)
            if len(pending) >= 500:
                Question.all_objects.bulk_update(
                    pending, ["source_kind", "source_anchor_seq"], batch_size=500,
                )
                pending.clear()
        if pending:
            Question.all_objects.bulk_update(
                pending, ["source_kind", "source_anchor_seq"], batch_size=500,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_question_recycle_bin"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="source_kind",
            field=models.CharField(
                choices=[
                    ("example", "例题"),
                    ("exercise", "练习"),
                    ("manual", "人工补录"),
                    ("unknown", "未分类"),
                ],
                default="unknown",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="source_anchor_seq",
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="questiondeletionbatch",
            name="origin",
            field=models.CharField(
                choices=[
                    ("user", "人工删除"),
                    ("resegment", "重新切题自动排除"),
                ],
                default="user",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="questiondeletionbatch",
            name="reason",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AddIndex(
            model_name="question",
            index=models.Index(
                fields=["paper", "group", "source_kind", "source_anchor_seq"],
                name="question_source_anchor",
            ),
        ),
        migrations.RunPython(backfill_source_identity, migrations.RunPython.noop),
    ]

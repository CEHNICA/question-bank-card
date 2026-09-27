import uuid

import django.db.models.deletion
from django.db import migrations, models


def backfill_question_identity_and_groups(apps, schema_editor):
    Question = apps.get_model("core", "Question")
    QuestionGroup = apps.get_model("core", "QuestionGroup")

    # Existing cards predate explicit question-number scopes. Keep them together in
    # one default exam group per paper; new book imports can add chapter/exercise groups.
    paper_ids = (
        Question.objects.order_by()
        .values_list("paper_id", flat=True)
        .distinct()
    )
    for paper_id in paper_ids.iterator():
        group = QuestionGroup.objects.create(
            paper_id=paper_id,
            title="默认题组",
            kind="exam",
            sequence=0,
        )
        Question.objects.filter(paper_id=paper_id, group_id__isnull=True).update(group_id=group.pk)

    # A UUID is deliberately unrelated to a displayed question number. Renumbering,
    # splitting a book into groups, or renaming a task therefore cannot change identity.
    pending = []
    for question in Question.objects.filter(source_key__isnull=True).iterator(chunk_size=1000):
        question.source_key = uuid.uuid4()
        pending.append(question)
        if len(pending) == 1000:
            Question.objects.bulk_update(pending, ["source_key"])
            pending.clear()
    if pending:
        Question.objects.bulk_update(pending, ["source_key"])


class Migration(migrations.Migration):
    dependencies = [("core", "0008_question_figure_review")]

    operations = [
        migrations.AddField(
            model_name="paper",
            name="archived",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="paper",
            name="material_type",
            field=models.CharField(
                choices=[("exam", "试卷"), ("book", "书籍")],
                default="exam",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="paper",
            name="structure",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name="paper",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "排队中"),
                    ("parsing", "MinerU 解析中"),
                    ("needs_grouping", "等待确认资料结构"),
                    ("segmenting", "切题中"),
                    ("reading", "AI 读题中"),
                    ("ready", "待你终审"),
                    ("failed", "失败"),
                ],
                default="queued",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="QuestionGroup",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("exam", "试卷"),
                            ("chapter", "章节"),
                            ("exercise", "练习"),
                            ("example", "例题"),
                            ("other", "其他"),
                        ],
                        default="exam",
                        max_length=16,
                    ),
                ),
                ("sequence", models.PositiveIntegerField(default=0)),
                ("page_start", models.PositiveIntegerField(blank=True, null=True)),
                ("page_end", models.PositiveIntegerField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "paper",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="question_groups",
                        to="core.paper",
                    ),
                ),
            ],
            options={"ordering": ["sequence", "id"]},
        ),
        migrations.CreateModel(
            name="ImportChunk",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("sequence", models.PositiveIntegerField()),
                ("source_page_start", models.PositiveIntegerField()),
                ("source_page_end", models.PositiveIntegerField()),
                ("page_map", models.JSONField(default=list)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "等待解析"),
                            ("parsing", "解析中"),
                            ("parsed", "解析完成"),
                            ("failed", "解析失败"),
                        ],
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("sha256", models.CharField(blank=True, default="", max_length=64)),
                ("artifact_path", models.CharField(blank=True, default="", max_length=500)),
                ("error", models.TextField(blank=True, default="")),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "paper",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="import_chunks",
                        to="core.paper",
                    ),
                ),
            ],
            options={"ordering": ["sequence", "id"]},
        ),
        migrations.AddConstraint(
            model_name="questiongroup",
            constraint=models.UniqueConstraint(
                fields=("paper", "sequence"), name="unique_group_sequence"
            ),
        ),
        migrations.AddConstraint(
            model_name="questiongroup",
            constraint=models.CheckConstraint(
                condition=models.Q(("page_end__isnull", True), ("page_start__isnull", True))
                | models.Q(
                    ("page_end__gte", models.F("page_start")),
                    ("page_end__isnull", False),
                    ("page_start__gte", 1),
                    ("page_start__isnull", False),
                ),
                name="valid_group_page_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="importchunk",
            constraint=models.UniqueConstraint(
                fields=("paper", "sequence"), name="unique_import_chunk_sequence"
            ),
        ),
        migrations.AddConstraint(
            model_name="importchunk",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("source_page_end__gte", models.F("source_page_start")),
                    ("source_page_start__gte", 1),
                ),
                name="valid_import_chunk_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="importchunk",
            constraint=models.CheckConstraint(
                condition=models.Q(("sequence__gte", 1)),
                name="valid_import_chunk_sequence",
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="questions",
                to="core.questiongroup",
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="source_key",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(backfill_question_identity_and_groups, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="question",
            name="source_key",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AddIndex(
            model_name="question",
            index=models.Index(
                fields=["paper", "group", "number"],
                name="question_source_lookup",
            ),
        ),
    ]

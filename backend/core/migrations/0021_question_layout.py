import uuid

from django.db import migrations, models
import django.db.models.deletion


def backfill_colors(apps, schema_editor):
    Question = apps.get_model("core", "Question")
    last_paper, next_color = None, 0
    rows = Question._base_manager.all().order_by("paper_id", "group_id", "number", "id")
    changed = []
    for row in rows.iterator():
        if row.paper_id != last_paper:
            last_paper, next_color = row.paper_id, 0
        row.color_index = next_color % 6
        next_color += 1
        changed.append(row)
        if len(changed) >= 500:
            Question._base_manager.bulk_update(changed, ["color_index"])
            changed = []
    if changed:
        Question._base_manager.bulk_update(changed, ["color_index"])


class Migration(migrations.Migration):
    dependencies = [("core", "0020_alter_question_state")]
    operations = [
        migrations.AddField(model_name="paper", name="layout_revision", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="paper", name="layout_page_epoch", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="question", name="color_index", field=models.PositiveSmallIntegerField(null=True, blank=True, default=None)),
        migrations.AlterField(model_name="questiondeletionbatch", name="origin", field=models.CharField(
            choices=[("user", "人工删除"), ("resegment", "重新切题自动排除"), ("layout", "原卷布局历史")], default="user", max_length=16)),
        migrations.CreateModel(name="QuestionLayoutOperation", fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("client_request_id", models.UUIDField()),
            ("request_hash", models.CharField(max_length=64)),
            ("kind", models.CharField(max_length=16)),
            ("source_ids", models.JSONField(default=list)), ("target_ids", models.JSONField(default=list)),
            ("before_snapshot", models.JSONField(default=dict)), ("after_snapshot", models.JSONField(default=dict)),
            ("after_fingerprints", models.JSONField(default=dict)),
            ("after_layout_revision", models.PositiveIntegerField(default=0)),
            ("page_epoch", models.PositiveIntegerField(default=0)),
            ("source_epoch", models.CharField(max_length=64, blank=True)),
            ("blocked_reason", models.CharField(max_length=300, blank=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("undone_at", models.DateTimeField(null=True, blank=True)),
            ("deletion_batch", models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, to="core.questiondeletionbatch")),
            ("paper", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="layout_operations", to="core.paper")),
        ], options={"ordering": ["-created_at", "-id"]}),
        migrations.AddConstraint(model_name="questionlayoutoperation", constraint=models.UniqueConstraint(
            fields=("paper", "client_request_id"), name="unique_layout_request")),
        migrations.RunPython(backfill_colors, migrations.RunPython.noop),
    ]

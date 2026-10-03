from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0017_libraryjob_executor_and_binding")]
    operations = [
        migrations.AddField(model_name="paper", name="processing_plan", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="question", name="body_mode", field=models.CharField(default="text", max_length=16)),
        migrations.AddField(model_name="question", name="processing_mode", field=models.CharField(default="auto", max_length=16)),
        migrations.AddField(model_name="question", name="content_revision", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="question", name="ocr_suggestion", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="question", name="ocr_pending", field=models.BooleanField(default=False)),
    ]

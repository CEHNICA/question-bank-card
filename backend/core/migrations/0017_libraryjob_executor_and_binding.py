from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0016_regionread_recommendation")]
    operations = [
        migrations.AddField(model_name="libraryjob", name="executor", field=models.CharField(choices=[("assistant", "当前 AI 助手"), ("api", "独立 API")], db_index=True, default="api", max_length=12)),
        migrations.AddField(model_name="libraryjob", name="fingerprint", field=models.CharField(blank=True, default="", max_length=64)),
        migrations.AddField(model_name="libraryjob", name="api_snapshot", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="libraryjob", name="agent", field=models.CharField(blank=True, default="", max_length=120)),
    ]

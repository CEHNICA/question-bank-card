from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0015_region_read")]

    operations = [
        migrations.AddField(
            model_name="regionread", name="recommendation",
            field=models.JSONField(default=dict, blank=True),
        ),
    ]

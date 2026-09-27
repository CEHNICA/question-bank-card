from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0007_paper_task_name")]

    operations = [
        migrations.AddField(
            model_name="question",
            name="figure_review",
            field=models.JSONField(default=dict),
        ),
    ]

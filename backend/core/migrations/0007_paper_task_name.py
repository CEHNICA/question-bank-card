from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_remove_ai_figure_descriptions"),
    ]

    operations = [
        migrations.AddField(
            model_name="paper",
            name="task_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]

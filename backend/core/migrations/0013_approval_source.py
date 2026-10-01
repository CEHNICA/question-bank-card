from django.db import migrations, models


def existing_approvals_are_human(apps, schema_editor):
    Question = apps.get_model("core", "Question")
    Question.objects.filter(approved=True).update(approval_source="human")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_block_table_html"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="approval_source",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddField(
            model_name="question",
            name="approval_agent",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="publishedquestion",
            name="review_source",
            field=models.CharField(default="human", max_length=8),
        ),
        migrations.AddField(
            model_name="publishedquestion",
            name="review_agent",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.RunPython(existing_approvals_are_human, migrations.RunPython.noop),
    ]

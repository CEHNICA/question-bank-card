import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0026_librarytagcatalogue_initialized")]

    operations = [
        migrations.AddField(model_name="librarytagsuggestion", name="catalogue_snapshot", field=models.JSONField(blank=True, default=dict)),
        migrations.CreateModel(
            name="LibraryTagRelation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("relation_type", models.CharField(choices=[("hierarchy", "上下级"), ("related_method", "相关解题方法")], default="hierarchy", max_length=20)),
                ("child", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="incoming_relations", to="core.librarytag")),
                ("parent", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="outgoing_relations", to="core.librarytag")),
            ],
            options={
                "ordering": ["relation_type", "parent_id", "child_id"],
                "indexes": [models.Index(fields=["relation_type", "parent"], name="tag_relation_type_parent")],
                "constraints": [models.UniqueConstraint(fields=("parent", "child", "relation_type"), name="unique_library_tag_relation"), models.CheckConstraint(condition=~models.Q(parent=models.F("child")), name="library_tag_relation_not_self")],
            },
        ),
    ]

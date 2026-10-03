import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0018_local_intake_and_image_body")]
    operations = [
        migrations.AddField(model_name="libraryjob", name="solution_scope", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="libraryjob", name="result", field=models.JSONField(blank=True, default=dict)),
        migrations.CreateModel(
            name="LibrarySolution",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("fingerprint", models.CharField(max_length=64)),
                ("answer", models.TextField(blank=True, default="")),
                ("analysis", models.TextField(blank=True, default="")),
                ("figures", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="core.librarysolution")),
                ("publication", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="solutions", to="core.publishedquestion")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]

"""Add display covering index and SQLite FTS5 virtual table for PublishedQuestion."""

from __future__ import annotations

from django.db import OperationalError, migrations, models


def _create_and_fill_fts(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "sqlite":
        return
    from core.library_fts import FTS_TABLE, tokenize_for_fts

    PublishedQuestion = apps.get_model("core", "PublishedQuestion")
    with schema_editor.connection.cursor() as cursor:
        try:
            cursor.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} "
                "USING fts5(pub_id, body, tokenize='unicode61 remove_diacritics 0')"
            )
        except OperationalError:
            return
        cursor.execute(f"DELETE FROM {FTS_TABLE}")
        batch: list[tuple[str, str]] = []
        rows = PublishedQuestion.objects.filter(status="published").values_list("id", "search_text")
        for pub_id, text in rows.iterator(chunk_size=1000):
            batch.append((pub_id.hex, tokenize_for_fts(text or "")))
            if len(batch) >= 1000:
                cursor.executemany(f"INSERT INTO {FTS_TABLE}(pub_id, body) VALUES (%s, %s)", batch)
                batch.clear()
        if batch:
            cursor.executemany(f"INSERT INTO {FTS_TABLE}(pub_id, body) VALUES (%s, %s)", batch)


def _drop_fts(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "sqlite":
        return
    from core.library_fts import FTS_TABLE

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {FTS_TABLE}")


class Migration(migrations.Migration):
    dependencies = [("core", "0034_alter_libraryjob_options_libraryjob_priority_and_more")]

    operations = [
        migrations.AddIndex(
            model_name="publishedquestion",
            index=models.Index(fields=["status", "number", "-published_at", "-id"], name="pubq_status_display"),
        ),
        migrations.RunPython(_create_and_fill_fts, _drop_fts),
    ]

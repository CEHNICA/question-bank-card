"""SQLite FTS5 full-text search index and incremental synchronization for PublishedQuestion."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from django.db import OperationalError, connection, models
from django.db.models.fields import UUIDField
from django.db.models.signals import post_delete, post_save

logger = logging.getLogger(__name__)

FTS_TABLE = "core_publishedquestion_fts"
_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_ALNUM_RUN_RE = re.compile(r"[a-z0-9]+")


class _QbFtsMatchLookup(models.Lookup):
    lookup_name = "qb_fts_match"
    prepare_rhs = False

    def get_prep_lookup(self):
        return str(self.rhs)

    def as_sql(self, compiler, conn):
        lhs, lhs_params = self.process_lhs(compiler, conn)
        rhs, rhs_params = self.process_rhs(compiler, conn)
        return f"{lhs} IN (SELECT pub_id FROM {FTS_TABLE} WHERE body MATCH {rhs})", [*lhs_params, *rhs_params]


UUIDField.register_lookup(_QbFtsMatchLookup)


def is_sqlite() -> bool:
    return connection.vendor == "sqlite"


def _pub_hex(pub_id: Any) -> str:
    return uuid.UUID(str(pub_id)).hex


def _table_exists(cursor: Any) -> bool:
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s", [FTS_TABLE])
    return cursor.fetchone() is not None


def ensure_table() -> bool:
    if not is_sqlite():
        return False
    try:
        with connection.cursor() as cursor:
            if _table_exists(cursor):
                return True
            cursor.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} "
                "USING fts5(pub_id, body, tokenize='unicode61 remove_diacritics 0')"
            )
        return True
    except OperationalError as exc:
        logger.warning("SQLite FTS5 table creation skipped: %s", exc)
        return False


def tokenize_for_fts(search_text: str) -> str:
    """Break normalized search_text into single CJK chars + ASCII full tokens and bigrams."""
    text = str(search_text or "").lower()
    if not text:
        return ""
    tokens: list[str] = _CJK_CHAR_RE.findall(text)
    for run in _ALNUM_RUN_RE.findall(text):
        tokens.append(run)
        if len(run) >= 2:
            for idx in range(len(run) - 1):
                tokens.append(run[idx : idx + 2])
    return " ".join(tokens)


def build_fts_query(key: str) -> str | None:
    """Build an FTS5 MATCH expression for one normalized search_key term."""
    text = str(key or "").lower()
    if not text:
        return None
    parts: list[str] = []
    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            parts.append(f'"{run}"')
        else:
            parts.append('"' + " ".join(run) + '"')
    for run in _ALNUM_RUN_RE.findall(text):
        if len(run) >= 2:
            for idx in range(len(run) - 1):
                parts.append(f'"{run[idx : idx + 2]}"')
    return " AND ".join(parts) if parts else None


def _delete_hex_ids(cursor: Any, hex_ids: list[str]) -> None:
    for hex_id in hex_ids:
        cursor.execute(
            f"DELETE FROM {FTS_TABLE} WHERE rowid IN (SELECT rowid FROM {FTS_TABLE} WHERE pub_id MATCH %s)",
            [f'"{hex_id}"'],
        )


def delete_publication(pub_id: Any) -> None:
    if not ensure_table():
        return
    try:
        hex_id = _pub_hex(pub_id)
    except (ValueError, TypeError, AttributeError):
        return
    with connection.cursor() as cursor:
        _delete_hex_ids(cursor, [hex_id])


def delete_publications(pub_ids: list[Any]) -> None:
    if not pub_ids or not ensure_table():
        return
    hex_ids: list[str] = []
    for pid in pub_ids:
        try:
            hex_ids.append(_pub_hex(pid))
        except (ValueError, TypeError, AttributeError):
            continue
    if not hex_ids:
        return
    with connection.cursor() as cursor:
        _delete_hex_ids(cursor, hex_ids)


def upsert_publication(pub_id: Any, search_text: str, status: str) -> None:
    if not ensure_table():
        return
    try:
        hex_id = _pub_hex(pub_id)
    except (ValueError, TypeError, AttributeError):
        return
    from .models import PublishedQuestion

    with connection.cursor() as cursor:
        _delete_hex_ids(cursor, [hex_id])
        if status == PublishedQuestion.Status.PUBLISHED:
            cursor.execute(
                f"INSERT INTO {FTS_TABLE}(pub_id, body) VALUES (%s, %s)", [hex_id, tokenize_for_fts(search_text)]
            )


def upsert_publications(publications: list[Any]) -> None:
    if not publications or not ensure_table():
        return
    from .models import PublishedQuestion

    hex_ids: list[str] = []
    inserts: list[tuple[str, str]] = []
    for pub in publications:
        try:
            hex_id = _pub_hex(pub.pk)
        except (ValueError, TypeError, AttributeError):
            continue
        hex_ids.append(hex_id)
        if getattr(pub, "status", None) == PublishedQuestion.Status.PUBLISHED:
            inserts.append((hex_id, tokenize_for_fts(getattr(pub, "search_text", "") or "")))
    if not hex_ids:
        return
    with connection.cursor() as cursor:
        _delete_hex_ids(cursor, hex_ids)
        if inserts:
            cursor.executemany(f"INSERT INTO {FTS_TABLE}(pub_id, body) VALUES (%s, %s)", inserts)


def rebuild_index() -> int:
    if not ensure_table():
        return 0
    from .models import PublishedQuestion

    total = 0
    rows_qs = PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).values_list(
        "id", "search_text"
    )
    with connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {FTS_TABLE}")
        batch: list[tuple[str, str]] = []
        for pub_id, text in rows_qs.iterator(chunk_size=1000):
            batch.append((_pub_hex(pub_id), tokenize_for_fts(text or "")))
            if len(batch) >= 1000:
                cursor.executemany(f"INSERT INTO {FTS_TABLE}(pub_id, body) VALUES (%s, %s)", batch)
                total += len(batch)
                batch.clear()
        if batch:
            cursor.executemany(f"INSERT INTO {FTS_TABLE}(pub_id, body) VALUES (%s, %s)", batch)
            total += len(batch)
    return total


def ensure_populated() -> bool:
    if not ensure_table():
        return False
    from .models import PublishedQuestion

    with connection.cursor() as cursor:
        cursor.execute(f"SELECT EXISTS(SELECT 1 FROM {FTS_TABLE})")
        has_rows = bool(cursor.fetchone()[0])
    if not has_rows and PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).exists():
        rebuild_index()
    return True


def apply_fts_search(rows: Any, raw_query: str) -> tuple[Any, str]:
    from . import library

    keys = [key for term in str(raw_query or "").split()[:8] if (key := library.search_key(term))]
    if not keys:
        return rows, "none"
    fts_parts = [part for part in (build_fts_query(k) for k in keys) if part]
    engine = "like"
    if fts_parts and is_sqlite() and ensure_populated():
        fts_expr = " AND ".join(fts_parts)
        rows = rows.filter(id__qb_fts_match=fts_expr)
        engine = "fts5"
    for key in keys:
        rows = rows.filter(search_text__contains=key)
    return rows, engine


def _on_publication_saved(sender: Any, instance: Any, **kwargs: Any) -> None:
    upsert_publication(instance.pk, getattr(instance, "search_text", "") or "", getattr(instance, "status", ""))


def _on_publication_deleted(sender: Any, instance: Any, **kwargs: Any) -> None:
    delete_publication(instance.pk)


def install_signals() -> None:
    from .models import PublishedQuestion

    post_save.connect(_on_publication_saved, sender=PublishedQuestion, dispatch_uid="qb_pub_fts_save")
    post_delete.connect(_on_publication_deleted, sender=PublishedQuestion, dispatch_uid="qb_pub_fts_delete")

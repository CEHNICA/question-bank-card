"""Read-only inspection of the tag-suggestion sweep.

Reports how many proposals were retired, when, and whether they are safe to bring
back. Never writes to the live database.
"""
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta, timezone

DB = os.path.join(os.environ["LOCALAPPDATA"], "QuestionBankCard", "db.sqlite3")
CN = timezone(timedelta(hours=8))


def cn(value):
    if not value:
        return "-"
    text = value.replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(CN).strftime("%Y-%m-%d %H:%M:%S")


def main():
    connection = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    cursor = connection.cursor()

    print("db:", DB)
    print("catalogue revision:", cursor.execute(
        "select revision from core_librarytagcatalogue").fetchone())
    print()
    print("== status / message ==")
    for status, message, count in cursor.execute(
            "select status, message, count(*) from core_librarytagsuggestion "
            "group by status, message order by count(*) desc"):
        print("%-9s %4d  %s" % (status, count, message))
    print()
    print("== stale, grouped by the minute they were retired ==")
    for minute, count in cursor.execute(
            "select coalesce(substr(resolved_at, 1, 16), '(no timestamp)') as bucket, count(*) "
            "from core_librarytagsuggestion where status = 'stale' "
            "and message = '标签目录已变化；旧建议保留在 AI 标签记录中。' "
            "group by bucket order by count(*) desc"):
        label = cn(minute + ":00") if minute != "(no timestamp)" else "(no timestamp)"
        print("  UTC %-20s (北京 %s)  %d" % (minute, label, count))
    print()

    swept = cursor.execute(
        "select id, publication_id, taxonomy_revision, prompt_version, resolved_at "
        "from core_librarytagsuggestion where status = 'stale' "
        "and message = '标签目录已变化；旧建议保留在 AI 标签记录中。'").fetchall()
    buckets = Counter((row[4] or "(none)")[:16] for row in swept)
    top_bucket, top_count = buckets.most_common(1)[0]
    print("largest retirement bucket: UTC %s = 北京 %s, %d rows"
          % (top_bucket, cn(top_bucket + ":00"), top_count))
    print("revisions among them:", Counter(row[2] for row in swept))
    print("prompt versions among them:", Counter(row[3] for row in swept))
    print()

    candidates = cursor.execute(
        "select count(*) from core_librarytagsuggestion where status = 'stale' "
        "and message = '标签目录已变化；旧建议保留在 AI 标签记录中。'").fetchone()[0]
    print("recoverable candidates (retired by the sweep):", candidates)

    # Would bringing them back be safe? A proposal is only un-acceptable if the tag it
    # points at is gone or switched off. Count the ones that would still be broken.
    alive = {str(pk): active for pk, active in cursor.execute(
        "select id, active from core_librarytag")}
    broken = 0
    empty = 0
    rows_read = 0
    for (entries,) in cursor.execute(
            "select suggestions from core_librarytagsuggestion where status = 'stale' "
            "and message = '标签目录已变化；旧建议保留在 AI 标签记录中。'"):
        rows_read += 1
        if isinstance(entries, str):
            entries = json.loads(entries or "[]")
        entries = [e for e in (entries or []) if isinstance(e, dict)]
        if not entries:
            empty += 1
        for entry in entries:
            for tag_id in [entry.get("tag_id")] + list(entry.get("parent_ids") or []):
                state = alive.get(str(tag_id))
                if state is not None and not state:
                    broken += 1
    print("  scanned rows:", rows_read)
    print("  of which point at a label that is now switched off:", broken)
    print("  of which carry no entries at all:", empty)

    print()
    print("== tags created that day (Beijing) ==")
    for _pk, name, kind, active, created in cursor.execute(
            "select id, name, kind, active, created_at from core_librarytag "
            "order by created_at desc limit 5"):
        print("  %s  %s  %s  active=%s" % (cn(created), name, kind, active))

    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

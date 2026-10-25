"""Read-only fingerprint of the question bank, for before/after an upgrade.

Counts only — never writes. Used to prove that installing a new build did not
change the teacher's data.
"""
import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

db = os.path.join(os.environ["LOCALAPPDATA"], "QuestionBankCard", "db.sqlite3")
conn = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
cur = conn.cursor()

def q(sql, *a):
    return cur.execute(sql, a).fetchone()[0]

snapshot = {
    "db_bytes": os.path.getsize(db),
    "papers": q("select count(*) from core_paper"),
    "questions_total": q("select count(*) from core_question"),
    "questions_approved": q("select count(*) from core_question where approved_at is not null"),
    "questions_deleted": q("select count(*) from core_question where deleted_at is not null"),
    "published_questions": q("select count(*) from core_publishedquestion"),
    "published_active": q(
        "select count(*) from core_publishedquestion where withdrawn_at is null"),
    "published_with_answer": q(
        "select count(*) from core_publishedquestion where search_text like '%答案%' or content like '%\"answer\": \"%'"),
    "tags": q("select count(*) from core_librarytag"),
    "tag_assignments": q("select count(*) from core_librarytagassignment"),
    "tag_catalogue_revision": q("select revision from core_librarytagcatalogue"),
    "tag_suggestions_pending": q(
        "select count(*) from core_librarytagsuggestion where status = 'pending'"),
    "tag_suggestions_total": q("select count(*) from core_librarytagsuggestion"),
}

# One number that changes if a single published question's text changed: an
# order-independent digest over every content hash. Counting rows would not
# notice an edit; this does.
import hashlib

digest = hashlib.sha256()
for (content_hash,) in cur.execute(
        "select content_hash from core_publishedquestion order by id"):
    digest.update((content_hash or "").encode("utf-8"))
    digest.update(b"\x00")
snapshot["published_content_digest"] = digest.hexdigest()[:16]

print(json.dumps(snapshot, ensure_ascii=False, indent=2))
conn.close()

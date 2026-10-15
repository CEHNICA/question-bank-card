"""Use two real SQLite file connections to reject competing layout drafts.

The child process owns a freshly migrated temporary database and only synthetic
paper rows. It has no worker, credentials, original user files or network access.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from django.test import SimpleTestCase


SQLITE_RACE = r'''
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from unittest import mock
import uuid

import requests
requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(AssertionError("Unexpected network"))
import django
django.setup()
from django.core.management import call_command
from django.db import connections
from core import question_layout as layout, readers
from core.models import Paper, Question, QuestionLayoutOperation

readers.chat = lambda *a, **k: (_ for _ in ()).throw(AssertionError("Unexpected model invocation"))
call_command("migrate", verbosity=0, interactive=False)
paper = Paper.objects.create(filename="synthetic-concurrency.pdf", kind="pdf", sha256="b" * 64,
    status="ready", pages=[{"page_idx": 0, "width": 595, "height": 842}])
questions = [Question.objects.create(paper=paper, number=number, processing_mode="manual",
    body_mode="text", stem=f"Synthetic preserved manual content {number}", edited=True,
    state="yellow", regions=[{"page_idx": 0, "bbox": [40, 100 * number, 900, 100 * number + 80]}])
    for number in (1, 2)]
paper.refresh_from_db()
revision = paper.layout_revision
payloads = [{"kind": "regions", "layout_revision": revision,
    "client_request_id": str(uuid.uuid4()),
    "sources": [{"id": q.pk, "revision": q.content_revision, "fingerprint": layout.fingerprint(q)}],
    "targets": [{"regions": [{"page_idx": 0, "bbox": [50, 100 * q.number, 910, 100 * q.number + 80]}]}]}
    for q in questions]
connections.close_all()
barrier = threading.Barrier(2)
physical_connections = set()
connection_lock = threading.Lock()
original_claim = layout._claim

def concurrent_claim(paper_id, expected):
    # No SQL or row-lock mocks: both callers enter the real conditional UPDATE
    # after finishing their request lookup on independent file connections.
    db = connections["default"]
    db.ensure_connection()
    with connection_lock:
        physical_connections.add(id(db.connection))
    barrier.wait(timeout=10)
    return original_claim(paper_id, expected)

def submit(payload):
    try:
        operation, repeated = layout.mutate(paper.pk, deepcopy(payload))
        return {"status": 200, "id": str(operation.pk), "repeated": repeated}
    except layout.LayoutError as error:
        return {"status": error.status, "reason": str(error)}
    finally:
        connections.close_all()

with mock.patch.object(layout, "_claim", concurrent_claim):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, payloads))
assert len(physical_connections) == 2, physical_connections
assert sorted(result["status"] for result in results) == [200, 409], results
assert QuestionLayoutOperation.objects.filter(paper=paper).count() == 1
paper.refresh_from_db()
assert paper.layout_revision == revision + 1, (revision, paper.layout_revision)
winner = next(i for i, result in enumerate(results) if result["status"] == 200)
loser = 1 - winner
for index, question in enumerate(questions):
    question.refresh_from_db()
    expected = payloads[index]["targets"][0]["regions"] if index == winner else [
        {"page_idx": 0, "bbox": [40, 100 * question.number, 900, 100 * question.number + 80]}]
    assert question.regions == expected, (index, question.regions)
    assert question.stem == f"Synthetic preserved manual content {question.number}"
    assert not question.ocr_pending and not question.reread_requested
stale = submit(payloads[loser])
assert stale["status"] == 409, stale
receipt = submit(payloads[winner])
assert receipt["status"] == 200 and receipt["repeated"], receipt
assert receipt["id"] == results[winner]["id"]
assert QuestionLayoutOperation.objects.filter(paper=paper).count() == 1
print(json.dumps({"real_connections": len(physical_connections), "results": results,
    "layout_revision": paper.layout_revision, "retry": receipt}, ensure_ascii=True))
'''


class QuestionLayoutSQLiteConcurrencyTests(SimpleTestCase):
    def test_two_real_file_connections_allow_one_commit_and_preserve_the_losing_draft(self):
        with tempfile.TemporaryDirectory(prefix="tiyouju-layout-sqlite-race-") as directory:
            root = Path(directory)
            environment = {
                key: value for key, value in os.environ.items()
                if not key.upper().startswith(("QB_", "TIYOUJU_", "DJANGO_", "PYTHONPATH"))
                and not any(token in key.upper() for token in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
            }
            environment.update({
                "DJANGO_SETTINGS_MODULE": "qb_server.settings",
                "QB_DATABASE": str(root / "synthetic.sqlite3"),
                "QB_DATA_ROOT": str(root / "data"),
                "QB_USER_ROOT": str(root / "user"),
                "QB_SECRETS_FILE": str(root / "absent-secrets.json"),
                "PYTHONIOENCODING": "utf-8",
            })
            result = subprocess.run([sys.executable, "-c", SQLITE_RACE],
                cwd=Path(__file__).resolve().parents[1], env=environment,
                capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
            evidence = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(evidence["real_connections"], 2)
            self.assertEqual(sorted(item["status"] for item in evidence["results"]), [200, 409])
            self.assertTrue(evidence["retry"]["repeated"])

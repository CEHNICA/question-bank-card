"""Build the practice paper that ships with the app (the 新手教学 demo).

The 12 cards were read once by the real pipeline (MinerU + a vision reader)
from ``tiyouju-demo-paper.pdf``.  Two teaching situations are then planted on
purpose so a new user can practise them:

* 第 9 题: the two readings disagree and the kept reading says “3 个单位”
  where the paper prints “5 个单位” – practise 改字.
* 第 2 题: the triangle figure is not bound yet – practise 配图.

Usage (from the repository root, after reading the demo paper into a
database with the app or ``qb_bench.py``)::

    QB_DATABASE=<db.sqlite3> QB_DATA_ROOT=<data> python docs/demo/build_demo_fixture.py

It writes ``backend/core/demo_data/demo-paper.json`` and copies the PDF next to
it.  The fixture holds no keys, no personal data and no machine paths.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")

import django  # noqa: E402

django.setup()

from core.models import Block, Paper, Question  # noqa: E402

TARGET = ROOT / "backend" / "core" / "demo_data"
PDF = Path(__file__).resolve().parent / "tiyouju-demo-paper.pdf"
QUESTION_FIELDS = (
    "number", "section", "question_type", "regions", "regions_auto", "start_source", "source_kind",
    "figure_candidates", "figures", "figure_review", "read_a", "read_b", "read_c", "stem", "options",
    "text_source", "state", "flags", "answer", "analysis",
)
MISREAD = ("向右移动 5 个单位", "向右移动 3 个单位")


def main() -> None:
    paper = Paper.objects.filter(filename="tiyouju-demo-paper.pdf", status=Paper.Status.READY).latest("created_at")
    questions = []
    for question in Question.objects.filter(paper=paper).order_by("number"):
        item = {field: getattr(question, field) for field in QUESTION_FIELDS}
        if question.number == 9:
            right = item["stem"]
            if MISREAD[0] not in right:
                raise SystemExit("第 9 题的题面和预期不同，请检查识读结果")
            wrong = right.replace(*MISREAD)
            item["stem"] = wrong
            item["read_a"] = {**(item["read_a"] or {}), "stem": wrong}
            if isinstance(item["read_a"].get("raw"), str):
                item["read_a"]["raw"] = item["read_a"]["raw"].replace(*MISREAD)
            item["read_b"] = {"engine": "MiniMax-M3", "stem": right, "options": item["options"],
                              "type": item["question_type"]}
            item["read_c"] = {}
            item["text_source"] = "a"
            item["state"] = "yellow"
            item["flags"] = ["两次识读不一致，请核对题面中标黄的位置"]
        if question.number in (4, 8) and len(item["regions"]) > 1:
            # The demo's decorative page banner is not part of these questions.
            item["regions"] = item["regions"][:1]
            item["regions_auto"] = item["regions"]
        if question.number == 2:
            # The reader did not bind the triangle: the card asks for its figure.
            item["figures"] = []
            item["figure_review"] = {}
            item["read_a"] = {key: value for key, value in (item["read_a"] or {}).items()
                              if key not in {"figures", "figures_followup"}}
        questions.append(item)
    blocks = [
        {"seq": block.seq, "type": block.type, "page_idx": block.page_idx, "bbox": block.bbox,
         "text": block.text, **({"html": block.html} if block.html else {})}
        for block in Block.objects.filter(paper=paper).order_by("seq")
    ]
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "demo-paper.json").write_text(json.dumps({
        "version": 1,
        "name": "示例试卷（新手练习用）",
        "filename": "示例试卷.pdf",
        "pages": paper.pages,
        "blocks": blocks,
        "questions": questions,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copyfile(PDF, TARGET / "demo-paper.pdf")
    print(f"{len(questions)} cards, {len(blocks)} blocks -> {TARGET}")


if __name__ == "__main__":
    main()

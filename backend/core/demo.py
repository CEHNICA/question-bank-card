"""The practice paper for 新手教学.

A new user can walk through the whole review once before uploading their own
paper.  The paper is an original, public demo (``docs/demo``) that was read
once by the real pipeline; the cards ship with the app, so opening it needs no
key, no network and no reading.  Two situations are planted for practice (see
``docs/demo/build_demo_fixture.py``).  A practice paper never enters the
formal library.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from django.conf import settings
from django.db import transaction

from .models import Block, Paper, Question

DEMO_MARK = "demo"
PUBLISH_REFUSED = "示例试卷只用来练习，不会进入正式题库。你自己的试卷核对后标记通过，就会自动进入题库。"


def data_root() -> Path:
    """The shipped fixture: next to this module, or under the installed app's resources."""
    candidates = [Path(__file__).resolve().parent / "demo_data"]
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        candidates.append(Path(frozen) / "backend" / "core" / "demo_data")
    for candidate in candidates:
        if (candidate / "demo-paper.json").is_file():
            return candidate
    raise FileNotFoundError("缺少示例试卷数据")


def is_demo(paper: Paper) -> bool:
    return bool((paper.structure or {}).get(DEMO_MARK))


def existing_demo() -> Paper | None:
    return next((paper for paper in Paper.objects.filter(archived=False).order_by("-created_at")
                 if is_demo(paper)), None)


def create_demo_paper(*, reset: bool = False) -> Paper:
    """Open the practice paper, making it (again) when needed."""
    current = existing_demo()
    if current is not None and not reset:
        return current
    if current is not None:
        remove_demo_paper(current)
    root = data_root()
    fixture = json.loads((root / "demo-paper.json").read_text(encoding="utf-8"))
    pdf = (root / "demo-paper.pdf").read_bytes()
    paper = Paper(
        filename=fixture["filename"], task_name=fixture["name"], kind="pdf",
        # Not the PDF's own digest: uploading the public demo PDF itself must
        # start a real reading, not open this practice copy.
        sha256=hashlib.sha256(pdf + b"\0qb-practice").hexdigest(),
        material_type=Paper.MaterialType.EXAM, pages=fixture["pages"],
        status=Paper.Status.READY, structure={DEMO_MARK: True},
    )
    folder = settings.DATA_ROOT / str(paper.id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "source.pdf").write_bytes(pdf)
    paper.source_path = str(folder / "source.pdf")
    paper.render_path = paper.source_path
    try:
        with transaction.atomic():
            paper.total = len(fixture["questions"])
            paper.progress = paper.total
            paper.save()
            Block.objects.bulk_create([Block(paper=paper, **block) for block in fixture["blocks"]])
            for item in fixture["questions"]:
                question = Question(paper=paper, **item)
                question.approved = False
                question.reread_requested = False
                question.save()
                if not item.get("figure_review"):
                    _derive_figure_review(question)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return paper


def _derive_figure_review(question: Question) -> None:
    """A card whose figure was left unbound shows the “可能漏图” prompt."""
    from .figure_policy import stored_or_derived_review
    from .views import _apply_figure_review

    _apply_figure_review(question, stored_or_derived_review(question))
    question.save(update_fields=["figure_review", "flags", "state"])


def remove_demo_paper(paper: Paper) -> None:
    folder = settings.DATA_ROOT / str(paper.id)
    paper.delete()
    shutil.rmtree(folder, ignore_errors=True)

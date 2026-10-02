"""Archive/restore affects visibility, never reviewed content or source history."""

import tempfile
import uuid
from pathlib import Path

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Block, ImportChunk, Paper, PublishedQuestion, Question, QuestionGroup


class PaperArchiveRestoreTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        override = override_settings(DATA_ROOT=self.root)
        override.enable()
        self.addCleanup(override.disable)
        self.paper = Paper.objects.create(
            filename="原始试卷.pdf", task_name="已核对试卷", kind="pdf", sha256="a" * 64,
            status=Paper.Status.READY, progress=1, total=1,
            pages=[{"page_idx": 0, "width": 595, "height": 842}],
            notes=["人工核对完成"], structure={"confirmed": True},
        )
        self.source = self.root / "source.pdf"
        self.source.write_bytes(b"immutable synthetic original")
        self.paper.source_path = str(self.source)
        self.paper.save(update_fields=["source_path"])

    def post(self, action, paper=None):
        return self.client.post(
            f"/api/papers/{(paper or self.paper).pk}/{action}", data="{}",
            content_type="application/json", HTTP_X_QB_REQUEST="1",
        )

    def test_archive_and_restore_preserve_original_review_and_every_publication(self):
        group = QuestionGroup.objects.create(paper=self.paper, title="第一章", sequence=0)
        question = Question.objects.create(
            paper=self.paper, group=group, number=1, stem="人工核对的 $x^2$",
            options={"A": "甲", "B": "乙"}, question_type="single_choice",
            answer="A", analysis="原卷解析", origin="2026练习卷", edited=True,
            state="green", approved=True, approved_at=timezone.now(), approval_source="human",
            approved_content_hash="c" * 64, text_source="human", type_locked=True,
            regions=[{"page_idx": 0, "bbox": [100, 100, 900, 300]}],
            read_a={"stem": "初读记录"}, figures=[], figure_review={"status": "manual_confirmed"},
        )
        Block.objects.create(paper=self.paper, seq=1, type="text", page_idx=0, text="原始解析块")
        ImportChunk.objects.create(paper=self.paper, sequence=1, source_page_start=1,
            source_page_end=1, page_map=[1], status="parsed", attempts=1)
        for version, status in ((1, "superseded"), (2, "published"), (3, "withdrawn")):
            PublishedQuestion.objects.create(question=question, paper=self.paper,
                source_filename=self.paper.filename, number=1, question_type="single_choice",
                version=version, status=status, content={"stem": f"历史版本{version}"},
                content_hash=str(version) * 64, extras={"tags": ["人工标注"]})

        before_paper = Paper.objects.filter(pk=self.paper.pk).values().get()
        before_related = {
            model: list(model.objects.filter(paper=self.paper).values())
            for model in (Question, QuestionGroup, Block, ImportChunk, PublishedQuestion)
        }
        archived = self.post("archive")
        self.assertEqual(archived.status_code, 200, archived.content)
        self.assertTrue(archived.json()["archived"])
        self.assertEqual(self.client.get("/api/papers").json()["papers"], [])
        viewed = self.client.get(f"/api/papers/{self.paper.pk}")
        self.assertEqual(viewed.status_code, 200, viewed.content)
        self.assertTrue(viewed.json()["paper"]["archived"])
        restored = self.post("restore")
        self.assertEqual(restored.status_code, 200, restored.content)
        self.assertTrue(restored.json()["restored"])
        self.assertFalse(restored.json()["paper"]["archived"])
        after_paper = Paper.objects.filter(pk=self.paper.pk).values().get()
        for key in before_paper:
            if key not in {"archived", "updated_at"}:
                self.assertEqual(after_paper[key], before_paper[key], key)
        for model, rows in before_related.items():
            self.assertEqual(list(model.objects.filter(paper=self.paper).values()), rows, model.__name__)
        self.assertEqual(self.source.read_bytes(), b"immutable synthetic original")
        self.assertEqual(self.client.get("/api/papers").json()["papers"][0]["id"], str(self.paper.pk))

    def test_restore_is_idempotent_and_does_not_resume_failed_or_pending_reads(self):
        self.paper.archived = True
        self.paper.status = Paper.Status.FAILED
        self.paper.error = "保留原来的暂停原因"
        self.paper.save()
        question = Question.objects.create(paper=self.paper, number=1, state="waiting", reread_requested=True)
        before_question = Question.objects.filter(pk=question.pk).values().get()
        first = self.post("restore")
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["restored"])
        self.paper.refresh_from_db()
        updated_at = self.paper.updated_at
        second = self.post("restore")
        self.assertFalse(second.json()["restored"])
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.FAILED)
        self.assertEqual(self.paper.error, "保留原来的暂停原因")
        self.assertEqual(self.paper.updated_at, updated_at)
        self.assertEqual(Question.objects.filter(pk=question.pk).values().get(), before_question)

    def test_archived_only_list_keeps_old_archives_reachable_and_paginates(self):
        self.paper.archived = True
        self.paper.save(update_fields=["archived"])
        Paper.objects.bulk_create([Paper(filename=f"活跃{i}.pdf", kind="pdf", sha256="b" * 64,
            status="ready") for i in range(201)])
        only = self.client.get("/api/papers?archived=only").json()
        self.assertEqual([row["id"] for row in only["papers"]], [str(self.paper.pk)])
        self.assertIsNone(only["next_offset"])
        Paper.objects.bulk_create([Paper(filename=f"归档{i}.pdf", kind="pdf", sha256="d" * 64,
            archived=True, status="ready") for i in range(200)])
        first = self.client.get("/api/papers?archived=only").json()
        second = self.client.get(f"/api/papers?archived=only&offset={first['next_offset']}").json()
        self.assertEqual(len(first["papers"]), 200)
        self.assertEqual(len(second["papers"]), 1)
        self.assertIsNone(second["next_offset"])
        self.assertEqual(len({row["id"] for row in first["papers"] + second["papers"]}), 201)
        self.assertTrue(all(row["archived"] for row in first["papers"] + second["papers"]))
        self.assertTrue(all(not row["archived"] for row in self.client.get("/api/papers").json()["papers"]))
        # Existing clients still use archived=1 to include archived and active papers.
        Paper.objects.create(filename="最新活跃.pdf", kind="pdf", sha256="e" * 64, status="ready")
        inclusive = self.client.get("/api/papers?archived=1").json()["papers"]
        self.assertTrue(any(not row["archived"] for row in inclusive))
        self.assertTrue(any(row["archived"] for row in inclusive))
        for offset in ("-1", "bad", "2147483648"):
            self.assertEqual(self.client.get(f"/api/papers?archived=only&offset={offset}").status_code, 400)

    def test_restore_requires_local_json_post_and_existing_paper(self):
        self.assertEqual(self.client.get(f"/api/papers/{self.paper.pk}/restore").status_code, 405)
        self.assertEqual(self.client.post(f"/api/papers/{self.paper.pk}/restore",
            data="{}", content_type="application/json").status_code, 403)
        self.assertEqual(self.client.post(f"/api/papers/{self.paper.pk}/restore",
            HTTP_X_QB_REQUEST="1").status_code, 415)
        self.assertEqual(self.client.post(f"/api/papers/{uuid.uuid4()}/restore", data="{}",
            content_type="application/json", HTTP_X_QB_REQUEST="1").status_code, 404)

    def test_existing_archive_guard_still_blocks_processing_and_recycle_bin(self):
        self.paper.status = Paper.Status.READING
        self.paper.save(update_fields=["status"])
        self.assertEqual(self.post("archive").status_code, 400)
        self.paper.status = Paper.Status.READY
        self.paper.save(update_fields=["status"])
        Question.all_objects.create(paper=self.paper, number=1, deleted_at=timezone.now())
        self.assertEqual(self.post("archive").status_code, 409)
        self.paper.refresh_from_db()
        self.assertFalse(self.paper.archived)

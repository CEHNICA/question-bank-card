import json
import tempfile
import uuid
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from . import library, pipeline, segment
from .models import (
    Block, Paper, PublishedQuestion, Question, QuestionDeletionBatch, QuestionGroup,
)


class QuestionRecycleBinApiTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        override = override_settings(DATA_ROOT=Path(self.temp.name))
        override.enable()
        self.addCleanup(override.disable)
        self.paper = Paper.objects.create(
            filename="试卷.pdf",
            kind="pdf",
            sha256="a" * 64,
            source_path=str(Path(self.temp.name) / "source.pdf"),
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
            status=Paper.Status.READY,
            progress=2,
            total=2,
        )
        self.group = QuestionGroup.objects.create(
            paper=self.paper,
            title="试卷.pdf",
            sequence=0,
            page_start=1,
            page_end=1,
            metadata={"pages": [0]},
        )
        self.q1 = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=1,
            stem="人工核对后的第一题",
            options={"A": "甲", "B": "乙"},
            question_type="single_choice",
            regions=[{"page_idx": 0, "bbox": [20, 100, 900, 260]}],
            regions_auto=[{"page_idx": 0, "bbox": [20, 90, 900, 260]}],
            read_a={"stem": "第一题"},
            edited=True,
            approved=True,
            approved_at=timezone.now(),
            approved_content_hash="1" * 64,
            state=Question.State.GREEN,
        )
        self.q2 = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=2,
            stem="第二题",
            regions=[{"page_idx": 0, "bbox": [20, 300, 900, 450]}],
            regions_auto=[{"page_idx": 0, "bbox": [20, 300, 900, 450]}],
            state=Question.State.YELLOW,
        )

    @staticmethod
    def _payload(response):
        return json.loads(response.content)

    def _post(self, path, payload):
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )

    def _delete(self, *ids):
        return self._post(
            f"/api/papers/{self.paper.pk}/questions/delete",
            {"question_ids": list(ids)},
        )

    def test_batch_delete_is_hidden_reversible_and_idempotent(self):
        preserved = Question.objects.filter(pk=self.q1.pk).values(
            "stem", "options", "regions", "regions_auto", "read_a", "edited",
            "approved", "approved_at", "approved_content_hash", "state",
        ).get()

        response = self._delete(self.q1.pk, self.q2.pk, self.q1.pk)
        self.assertEqual(response.status_code, 200, response.content)
        data = self._payload(response)
        batch_id = data["undo_batch"]["id"]
        self.assertEqual(data["deleted"], 2)
        self.assertEqual(data["paper"]["counts"]["total"], 0)
        self.assertEqual(data["paper"]["trash_count"], 2)
        self.assertEqual(Question.objects.filter(paper=self.paper).count(), 0)
        self.assertEqual(Question.all_objects.filter(paper=self.paper).count(), 2)
        self.assertEqual(
            Question.all_objects.filter(pk=self.q1.pk).values(*preserved.keys()).get(),
            preserved,
        )

        detail = self.client.get(f"/api/papers/{self.paper.pk}").json()
        self.assertEqual(detail["questions"], [])
        self.assertEqual(detail["paper"]["total"], 0)
        trash = self.client.get(f"/api/papers/{self.paper.pk}/question-trash").json()
        self.assertEqual(len(trash["batches"]), 1)
        self.assertEqual(trash["batches"][0]["count"], 2)

        # A stale single-card request is not the same user gesture.  Returning
        # the two-card batch here would make its Undo restore more than asked.
        subset_retry = self.client.delete(
            f"/api/questions/{self.q1.pk}", HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(subset_retry.status_code, 409)

        repeated = self._delete(self.q1.pk, self.q2.pk)
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertEqual(repeated.json()["deleted"], 0)
        self.assertEqual(repeated.json()["undo_batch"]["id"], batch_id)
        self.assertEqual(QuestionDeletionBatch.objects.count(), 1)

        restore_url = f"/api/papers/{self.paper.pk}/question-trash/{batch_id}/restore"
        restored = self._post(restore_url, {})
        self.assertEqual(restored.status_code, 200, restored.content)
        self.assertEqual(restored.json()["restored"], 2)
        self.assertEqual(restored.json()["paper"]["counts"]["total"], 2)
        self.assertEqual(restored.json()["paper"]["trash_count"], 0)
        self.assertEqual(Question.objects.filter(paper=self.paper).count(), 2)
        self.assertEqual(
            Question.objects.filter(pk=self.q1.pk).values(*preserved.keys()).get(),
            preserved,
        )
        self.assertIsNone(Question.objects.get(pk=self.q1.pk).deletion_batch_id)

        repeated_restore = self._post(restore_url, {})
        self.assertEqual(repeated_restore.status_code, 200, repeated_restore.content)
        self.assertEqual(repeated_restore.json()["restored"], 0)
        self.assertTrue(repeated_restore.json()["already_restored"])

    def test_batch_is_atomic_across_papers_and_rejects_active_publication(self):
        other = Paper.objects.create(
            filename="另一份.pdf", kind="pdf", sha256="b" * 64,
            source_path="other.pdf", status=Paper.Status.READY,
        )
        other_q = Question.objects.create(
            paper=other, number=1, stem="另一题", state=Question.State.GREEN,
        )
        response = self._delete(self.q1.pk, other_q.pk)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Question.objects.filter(pk__in=[self.q1.pk, other_q.pk]).count(), 2)
        self.assertFalse(QuestionDeletionBatch.objects.exists())

        publication = PublishedQuestion.objects.create(
            id=uuid.uuid4(),
            question=self.q2,
            paper=self.paper,
            source_filename=self.paper.display_name,
            number=2,
            question_type="unknown",
            version=1,
            status=PublishedQuestion.Status.PUBLISHED,
            content={},
            content_hash="2" * 64,
        )
        response = self._delete(self.q1.pk, self.q2.pk)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Question.objects.filter(pk__in=[self.q1.pk, self.q2.pk]).count(), 2)
        publication.refresh_from_db()
        self.assertEqual(publication.status, PublishedQuestion.Status.PUBLISHED)
        self.assertFalse(QuestionDeletionBatch.objects.exists())

    def test_delete_requires_ready_finished_cards(self):
        for status in (
            Paper.Status.QUEUED,
            Paper.Status.PARSING,
            Paper.Status.SEGMENTING,
            Paper.Status.READING,
            Paper.Status.FAILED,
            Paper.Status.NEEDS_GROUPING,
        ):
            with self.subTest(status=status):
                Paper.objects.filter(pk=self.paper.pk).update(status=status)
                self.paper.refresh_from_db()
                response = self._delete(self.q1.pk)
                self.assertEqual(response.status_code, 409)
                self.assertTrue(Question.objects.filter(pk=self.q1.pk).exists())
        Paper.objects.filter(pk=self.paper.pk).update(status=Paper.Status.READY)
        Question.objects.filter(pk=self.q1.pk).update(state=Question.State.READING)
        self.assertEqual(self._delete(self.q1.pk).status_code, 409)
        Question.objects.filter(pk=self.q1.pk).update(
            state=Question.State.GREEN,
            reread_requested=True,
        )
        self.assertEqual(self._delete(self.q1.pk).status_code, 409)
        self.assertFalse(QuestionDeletionBatch.objects.exists())

    def test_single_delete_endpoint_is_soft_and_repeatable(self):
        path = f"/api/questions/{self.q2.pk}"
        response = self.client.delete(path, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 200, response.content)
        batch_id = response.json()["undo_batch"]["id"]
        self.assertFalse(response.json()["already_deleted"])
        self.assertFalse(Question.objects.filter(pk=self.q2.pk).exists())
        self.assertTrue(Question.all_objects.filter(pk=self.q2.pk).exists())

        repeated = self.client.delete(path, HTTP_X_QB_REQUEST="1")
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertTrue(repeated.json()["already_deleted"])
        self.assertEqual(repeated.json()["undo_batch"]["id"], batch_id)

    def test_withdrawn_snapshot_survives_draft_delete_and_publish_skips_deleted(self):
        publication = PublishedQuestion.objects.create(
            id=uuid.uuid4(),
            question=self.q1,
            paper=self.paper,
            source_filename=self.paper.display_name,
            number=1,
            question_type="unknown",
            version=1,
            status=PublishedQuestion.Status.WITHDRAWN,
            content={"stem": self.q1.stem},
            content_hash="3" * 64,
        )
        response = self._delete(self.q1.pk)
        self.assertEqual(response.status_code, 200, response.content)
        publication.refresh_from_db()
        self.assertEqual(publication.question_id, self.q1.pk)
        with self.assertRaisesMessage(ValueError, "回收站"):
            library.publish(self.q1)  # stale in-memory object must not bypass the tombstone
        publish = self._post(f"/api/papers/{self.paper.pk}/publish", {})
        self.assertEqual(publish.status_code, 200, publish.content)
        self.assertEqual(publish.json()["created"], 0)
        self.assertEqual(PublishedQuestion.objects.filter(question_id=self.q1.pk).count(), 1)

    def test_tombstone_blocks_manual_duplicate_and_hard_task_delete_cascades(self):
        self.assertEqual(self._delete(self.q1.pk).status_code, 200)
        add = self._post(f"/api/papers/{self.paper.pk}/questions", {
            "number": 1,
            "group_id": self.group.pk,
            "regions": [{"page_idx": 0, "bbox": [20, 100, 900, 260]}],
        })
        self.assertEqual(add.status_code, 400, add.content)
        self.assertIn("回收站", add.json()["error"])

        delete_paper = self.client.delete(
            f"/api/papers/{self.paper.pk}",
            HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(delete_paper.status_code, 200, delete_paper.content)
        self.assertFalse(Question.all_objects.filter(pk=self.q1.pk).exists())
        self.assertFalse(QuestionDeletionBatch.objects.exists())

    def test_task_rename_keeps_deleted_approved_card_current_after_restore(self):
        self.q1.approved_content_hash = library.approval_hash(self.q1)
        self.q1.save(update_fields=["approved_content_hash"])
        deleted = self._delete(self.q1.pk).json()
        batch_id = deleted["undo_batch"]["id"]

        renamed = self.client.patch(
            f"/api/papers/{self.paper.pk}",
            data=json.dumps({"name": "新任务名"}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(renamed.status_code, 200, renamed.content)
        restored = self._post(
            f"/api/papers/{self.paper.pk}/question-trash/{batch_id}/restore", {},
        )
        self.assertEqual(restored.status_code, 200, restored.content)
        question = Question.objects.select_related("paper", "group").get(pk=self.q1.pk)
        self.assertTrue(library.approval_is_current(question))

    def test_archive_and_photo_page_order_are_blocked_until_trash_is_restored(self):
        deleted = self._delete(self.q1.pk)
        self.assertEqual(deleted.status_code, 200, deleted.content)

        archived = self._post(f"/api/papers/{self.paper.pk}/archive", {})
        self.assertEqual(archived.status_code, 409, archived.content)
        self.assertIn("回收站", archived.json()["error"])
        self.paper.refresh_from_db()
        self.assertFalse(self.paper.archived)

        self.paper.pages = [
            {"page_idx": 0, "width": 1000, "height": 1000},
            {"page_idx": 1, "width": 1000, "height": 1000},
        ]
        self.paper.photos = {
            "files": [
                {"name": "01.jpg", "file": "01.jpg"},
                {"name": "02.jpg", "file": "02.jpg"},
            ],
            "order": [0, 1],
            "notes": [],
            "check": "",
        }
        self.paper.save(update_fields=["pages", "photos"])
        Block.objects.create(
            paper=self.paper, seq=1, type="text", page_idx=0,
            bbox=[20, 20, 900, 200], text="1. 第一题",
        )
        reordered = self._post(
            f"/api/papers/{self.paper.pk}/page-order", {"order": [1, 0]},
        )
        self.assertEqual(reordered.status_code, 409, reordered.content)
        self.assertIn("回收站", reordered.json()["error"])
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.READY)
        self.assertTrue(Question.all_objects.filter(pk=self.q1.pk, deleted_at__isnull=False).exists())

    def test_trash_listing_does_not_hide_batches_older_than_one_hundred(self):
        now = timezone.now()
        for index in range(101):
            batch = QuestionDeletionBatch.objects.create(
                paper=self.paper,
                question_ids=[],
            )
            question = Question.all_objects.create(
                paper=self.paper,
                group=self.group,
                number=1000 + index,
                stem=f"回收站题卡 {index}",
                state=Question.State.GREEN,
                deleted_at=now,
                deletion_batch=batch,
            )
            batch.question_ids = [question.pk]
            batch.save(update_fields=["question_ids"])

        trash = self.client.get(f"/api/papers/{self.paper.pk}/question-trash")
        self.assertEqual(trash.status_code, 200, trash.content)
        self.assertEqual(len(trash.json()["batches"]), 101)

    def test_split_is_blocked_while_recycle_bin_has_cards(self):
        self.assertEqual(self._delete(self.q1.pk).status_code, 200)
        Block.objects.create(
            paper=self.paper, seq=1, type="text", page_idx=0,
            bbox=[20, 20, 900, 200], text="1. 第一题",
        )
        resegment = self._post(f"/api/papers/{self.paper.pk}/resegment", {})
        self.assertEqual(resegment.status_code, 409, resegment.content)
        self.assertIn("回收站", resegment.json()["error"])
        self.paper.pages = [
            {"page_idx": 0, "width": 1000, "height": 1000},
            {"page_idx": 1, "width": 1000, "height": 1000},
        ]
        self.paper.save(update_fields=["pages"])
        response = self._post(
            f"/api/papers/{self.paper.pk}/split",
            {"groups": [[0], [1]]},
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("回收站", response.json()["error"])
        self.paper.refresh_from_db()
        self.assertFalse(self.paper.archived)

    def test_page_reorder_remaps_deleted_card_without_restoring_it(self):
        self.assertEqual(self._delete(self.q1.pk).status_code, 200)
        pages = [
            {"page_idx": 0, "width": 1000, "height": 1000},
            {"page_idx": 1, "width": 1000, "height": 1000},
        ]
        self.paper.pages = pages
        self.paper.render_path = str(Path(self.temp.name) / "pages.pdf")
        self.paper.photos = {
            "files": [
                {"name": "01.jpg", "file": "01.jpg"},
                {"name": "02.jpg", "file": "02.jpg"},
            ],
            "order": [0, 1],
            "notes": [],
            "check": "",
        }
        self.paper.save(update_fields=["pages", "render_path", "photos"])

        with mock.patch.object(pipeline.photos, "build_pdf"), \
                mock.patch.object(pipeline, "clear_page_cache"), \
                mock.patch.object(pipeline.imaging, "page_sizes", return_value=pages), \
                mock.patch.object(pipeline.photos, "page_ranges", return_value={0: None, 1: None}), \
                mock.patch.object(pipeline, "_plan_structure", return_value=({}, False)):
            pipeline.reorder_photo_pages(self.paper, [1, 0])

        deleted = Question.all_objects.get(pk=self.q1.pk)
        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(deleted.regions[0]["page_idx"], 1)
        self.assertEqual(deleted.regions_auto[0]["page_idx"], 1)
        self.assertFalse(Question.objects.filter(pk=self.q1.pk).exists())

    def test_group_rebuild_moves_deleted_card_with_its_source_page(self):
        self.assertEqual(self._delete(self.q1.pk).status_code, 200)
        self.paper.pages = [
            {"page_idx": 0, "width": 1000, "height": 1000},
            {"page_idx": 1, "width": 1000, "height": 1000},
        ]
        self.paper.structure = {
            "confirmed": True,
            "confirmed_groups": [[0], [1]],
            "groups_need_rebuild": True,
        }
        self.paper.save(update_fields=["pages", "structure"])
        self.group.page_start = 1
        self.group.page_end = 2
        self.group.metadata = {"pages": [0, 1]}
        self.group.save(update_fields=["page_start", "page_end", "metadata"])
        Question.all_objects.filter(pk=self.q1.pk).update(
            regions=[{"page_idx": 1, "bbox": [20, 100, 900, 260]}],
            regions_auto=[{"page_idx": 1, "bbox": [20, 100, 900, 260]}],
        )

        groups = pipeline._ensure_question_groups(self.paper)

        self.assertEqual(len(groups), 2)
        deleted = Question.all_objects.get(pk=self.q1.pk)
        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(deleted.group.metadata["pages"], [1])
        self.assertFalse(Question.objects.filter(pk=self.q1.pk).exists())


class QuestionRecycleBinResegmentTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        override = override_settings(DATA_ROOT=Path(self.temp.name))
        override.enable()
        self.addCleanup(override.disable)
        self.paper = Paper.objects.create(
            filename="重复题号.pdf",
            kind="pdf",
            sha256="c" * 64,
            source_path="source.pdf",
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
            status=Paper.Status.READY,
            progress=2,
            total=2,
        )
        self.group = QuestionGroup.objects.create(
            paper=self.paper,
            title="练习",
            sequence=0,
            page_start=1,
            page_end=1,
            metadata={"pages": [0]},
        )
        Block.objects.create(
            paper=self.paper,
            seq=1,
            type="text",
            page_idx=0,
            bbox=[10, 10, 900, 900],
            text="1. 第一处  1. 第二处",
        )
        self.first_regions = [{"page_idx": 0, "bbox": [40, 120, 470, 220]}]
        self.second_regions = [{"page_idx": 0, "bbox": [40, 320, 470, 420]}]
        self.first = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=1,
            section="第一处",
            stem="第一处题目",
            regions=self.first_regions,
            regions_auto=self.first_regions,
            state=Question.State.GREEN,
        )
        self.second = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=1,
            section="第二处",
            stem="第二处题目",
            regions=self.second_regions,
            regions_auto=self.second_regions,
            state=Question.State.GREEN,
        )

    def test_deleted_duplicate_is_a_tombstone_and_nearest_active_card_stays_stable(self):
        response = self.client.post(
            f"/api/papers/{self.paper.pk}/questions/delete",
            data=json.dumps({"question_ids": [self.first.pk]}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(response.status_code, 200, response.content)
        second_key = self.second.source_key
        self.paper.status = Paper.Status.SEGMENTING
        self.paper.save(update_fields=["status"])
        items = [
            {
                "number": 1,
                "section": "第一处更新",
                "question_type": "free_response",
                "regions": self.first_regions,
                "figure_candidates": [],
                "start": {"source": "mineru"},
            },
            {
                "number": 1,
                "section": "第二处更新",
                "question_type": "free_response",
                "regions": self.second_regions,
                "figure_candidates": [],
                "start": {"source": "mineru"},
            },
        ]
        leading = mock.Mock(message="")
        with mock.patch.object(segment, "analyse", return_value=(object(), [])), \
                mock.patch.object(segment, "repair_leading_question", return_value=([], leading)), \
                mock.patch.object(pipeline, "locate_missing", return_value=[]), \
                mock.patch.object(segment, "build_questions", return_value=items), \
                mock.patch.object(segment, "text_blocks_in", return_value=[1]), \
                mock.patch.object(pipeline.imaging, "trim_regions", side_effect=lambda value, _load: value):
            pipeline.segment_paper(self.paper)

        self.assertEqual(Question.all_objects.filter(paper=self.paper).count(), 2)
        self.assertEqual(Question.objects.filter(paper=self.paper).count(), 1)
        deleted = Question.all_objects.get(pk=self.first.pk)
        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(deleted.section, "第一处")
        active = Question.objects.get(pk=self.second.pk)
        self.assertEqual(active.source_key, second_key)
        self.assertEqual(active.regions, self.second_regions)
        self.assertEqual(active.section, "第二处更新")

"""正式题库与试卷统一回收站后端接口测试。"""

import json

from django.conf import settings
from django.test import Client, TestCase

from . import library
from .models import Paper, PublishedQuestion
from .test_v110_types_origin import TempDataMixin


class LibraryTrashApiTests(TempDataMixin, TestCase):
    def setUp(self) -> None:
        self.use_temp_data()
        self.paper = self.make_paper(task_name="初三数学模拟卷.pdf")
        self.q1 = self.card(self.paper, number=1, question_type="single_choice", stem="已知 $x=1$。")
        self.q2 = self.card(self.paper, number=2, question_type="free_response", stem="求正方形面积。")
        self.client = Client()
        self.assertEqual(self._post(f"/api/questions/{self.q1.pk}/approve", {"approved": True}).status_code, 200)
        self.assertEqual(self._post(f"/api/questions/{self.q2.pk}/approve", {"approved": True}).status_code, 200)
        self.pub1 = self.q1.publications.get(status=PublishedQuestion.Status.PUBLISHED)
        self.pub2 = self.q2.publications.get(status=PublishedQuestion.Status.PUBLISHED)

    def _post(self, path: str, payload: dict | None = None):
        return self.client.post(
            path,
            json.dumps(payload or {}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )

    def test_trash_lists_withdrawn_questions_and_archived_papers(self) -> None:
        library.withdraw(self.pub1)
        archived_res = self._post(f"/api/papers/{self.paper.pk}/archive")
        self.assertEqual(archived_res.status_code, 200)

        resp = self.client.get("/api/library/trash")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["counts"]["papers"], 1)
        self.assertEqual(body["counts"]["questions"], 1)
        self.assertEqual(body["archived_papers"][0]["id"], str(self.paper.pk))
        self.assertEqual(body["withdrawn_questions"][0]["id"], str(self.pub1.pk))

    def test_restore_and_restore_batch_republish_withdrawn_questions(self) -> None:
        library.withdraw(self.pub1)
        library.withdraw(self.pub2)

        single = self._post(f"/api/library/{self.pub1.pk}/restore")
        self.assertEqual(single.status_code, 200)
        self.pub1.refresh_from_db()
        self.assertEqual(self.pub1.status, PublishedQuestion.Status.PUBLISHED)
        self.assertIsNone(self.pub1.withdrawn_at)

        batch = self._post("/api/library/restore-batch", {"ids": [str(self.pub2.pk), str(self.pub1.pk)]})
        self.assertEqual(batch.status_code, 200)
        self.assertEqual(batch.json()["restored_count"], 2)
        self.pub2.refresh_from_db()
        self.assertEqual(self.pub2.status, PublishedQuestion.Status.PUBLISHED)

    def test_restore_rejects_superseded_when_newer_live_version_exists(self) -> None:
        self.q1.stem = "新版题干：$x=2$。"
        self.q1.save(update_fields=["stem"])
        self.assertEqual(self._post(f"/api/questions/{self.q1.pk}/approve", {"approved": True}).status_code, 200)
        self.pub1.refresh_from_db()
        self.assertEqual(self.pub1.status, PublishedQuestion.Status.SUPERSEDED)
        newer = self.q1.publications.get(status=PublishedQuestion.Status.PUBLISHED)

        resp = self._post(f"/api/library/{self.pub1.pk}/restore")
        self.assertEqual(resp.status_code, 409)
        self.assertIn("已被新版替代", resp.json()["error"])
        self.assertEqual(newer.status, PublishedQuestion.Status.PUBLISHED)

    def test_purge_requires_withdrawn_status_and_cleans_snapshot_folder(self) -> None:
        # 在库试题不能直接跳过回收站彻底删除
        blocked = self._post(f"/api/library/{self.pub1.pk}/purge")
        self.assertEqual(blocked.status_code, 409)
        self.assertTrue(PublishedQuestion.objects.filter(pk=self.pub1.pk).exists())

        snap_dir = settings.DATA_ROOT / "library" / str(self.pub1.pk)
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "figure-1.png").write_bytes(b"fake-png")

        library.withdraw(self.pub1)
        purged = self._post(f"/api/library/{self.pub1.pk}/purge")
        self.assertEqual(purged.status_code, 200)
        self.assertFalse(PublishedQuestion.objects.filter(pk=self.pub1.pk).exists())
        self.assertFalse(snap_dir.exists())

    def test_purge_batch_reports_succeeded_and_skipped(self) -> None:
        library.withdraw(self.pub1)
        # pub2 仍在库，批量 purge 时应跳过 pub2 而非全批失败
        resp = self._post("/api/library/purge-batch", {"ids": [str(self.pub1.pk), str(self.pub2.pk)]})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["purged"], [str(self.pub1.pk)])
        self.assertEqual(body["purged_count"], 1)
        self.assertEqual(len(body["skipped"]), 1)
        self.assertEqual(body["skipped"][0]["id"], str(self.pub2.pk))

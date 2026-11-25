"""Tests for tag assignment timestamps, lifecycle preservation, P0 edit carry-over, and tag-age browse."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from . import library, library_browse, library_question_editor, library_similarity, library_tagging, library_transfer
from .models import LibraryTag, LibraryTagAssignment, Paper, PublishedQuestion, Question


class TagTimestampsAndLifecycleTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        state = override_settings(DATA_ROOT=self.root)
        state.enable()
        self.addCleanup(state.disable)
        env = mock.patch.dict(
            os.environ,
            {
                "QB_FEATURES_FILE": str(self.root / "features.json"),
                "QB_LIBRARY_AI_SETTINGS_FILE": str(self.root / "ai.json"),
                "QB_LIBRARY_AI_CREDENTIAL_FILE": str(self.root / "key.dat"),
            },
        )
        env.start()
        self.addCleanup(env.stop)
        (self.root / "features.json").write_text(
            json.dumps({"ai_answer": False, "knowledge_tags": False}), encoding="utf-8"
        )
        self.paper = self._make_paper("2025海淀期中数学.png")

    def _make_paper(self, filename: str) -> Paper:
        source = self.root / f"src-{uuid.uuid4().hex[:8]}.png"
        Image.new("RGB", (200, 260), "white").save(source)
        paper = Paper.objects.create(
            filename=filename,
            kind="image",
            source_path=str(source),
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            status=Paper.Status.READY,
            pages=[{"page_idx": 0, "width": 200, "height": 260}],
        )
        pages_dir = self.root / str(paper.pk) / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (200, 260), "white").save(pages_dir / "page_0.png")
        return paper

    def _make_pub(self, number: int, stem: str, *, paper: Paper | None = None) -> tuple[Question, PublishedQuestion]:
        target_paper = paper or self.paper
        q = Question.objects.create(
            paper=target_paper,
            number=number,
            question_type="single_choice",
            stem=stem,
            options={"A": "1", "B": "2", "C": "3", "D": "4"},
            answer="A",
            origin="2025海淀期中",
            regions=[{"page_idx": 0, "bbox": [0, 0, 100, 100]}],
            state=Question.State.GREEN,
        )
        library.approve(q, now=timezone.now())
        q.save()
        pub, _ = library.publish(q)
        return q, pub

    def _make_tag(self, name: str, kind: str = LibraryTag.Kind.KNOWLEDGE) -> LibraryTag:
        return LibraryTag.objects.create(name=name, kind=kind, active=True)

    def _attach_with_time(
        self, pub: PublishedQuestion, tag: LibraryTag, created_at, *, source=LibraryTagAssignment.Source.HUMAN
    ) -> LibraryTagAssignment:
        row = LibraryTagAssignment.objects.create(publication=pub, tag=tag, source=source, scope={"whole": True})
        library_tagging.restore_assignment_timestamps(row, created_at=created_at, updated_at=created_at)
        library_tagging._sync_projection(pub.pk)
        return LibraryTagAssignment.objects.get(pk=row.pk)

    def test_timestamps_exposed_in_full_slim_and_assignment_endpoints(self):
        _, pub = self._make_pub(1, "已知 $f(x)=x^2-2x$，求最小值。")
        tag = self._make_tag("不会")
        old_time = timezone.now() - timedelta(days=400)
        self._attach_with_time(pub, tag, old_time)

        full = library.publication_json(pub)
        self.assertEqual(len(full["tag_assignments"]), 1)
        self.assertEqual(full["tag_assignments"][0]["created_at"], old_time.isoformat())
        self.assertEqual(full["tag_assignments"][0]["updated_at"], old_time.isoformat())

        res_slim = self.client.get("/api/library?bulk=1&limit=10&offset=0&slim=1", HTTP_X_QB_REQUEST="1")
        self.assertEqual(res_slim.status_code, 200)
        item = res_slim.json()["items"][0]
        self.assertEqual(item["tag_assignments"][0]["created_at"], old_time.isoformat())
        self.assertEqual(item["tag_assignments"][0]["updated_at"], old_time.isoformat())

        res_single = self.client.get(f"/api/library/{pub.pk}/tags/assign", HTTP_X_QB_REQUEST="1")
        self.assertEqual(res_single.status_code, 200)
        self.assertEqual(res_single.json()["assignments"][0]["created_at"], old_time.isoformat())

        res_batch = self.client.get(f"/api/library/tags/assignments/batch?ids={pub.pk}", HTTP_X_QB_REQUEST="1")
        self.assertEqual(res_batch.status_code, 200)
        batch_entry = res_batch.json()["items"][str(pub.pk)]["assignments"][0]
        self.assertEqual(batch_entry["created_at"], old_time.isoformat())

    def test_editing_published_question_carries_tags_and_preserves_original_created_at(self):
        q, pub_v1 = self._make_pub(2, "已知 $x^2=4$，求 $x$ 的值。")
        tag_hard = self._make_tag("不会")
        old_time = timezone.now() - timedelta(days=730)
        self._attach_with_time(pub_v1, tag_hard, old_time)

        editor_data = library_question_editor.editor_data(pub_v1)
        payload = {
            "revision": editor_data["revision"],
            "content_hash": editor_data["content_hash"],
            "stem": "已知 $x^2=9$，求 $x$ 的正值。",
            "options": editor_data["options"],
            "question_type": editor_data["question_type"],
            "body_mode": editor_data["body_mode"],
        }
        pub_v2, created, _ = library_question_editor.save(pub_v1, payload)
        self.assertTrue(created)
        self.assertNotEqual(pub_v1.pk, pub_v2.pk)

        carried = LibraryTagAssignment.objects.filter(publication=pub_v2, tag=tag_hard).first()
        self.assertIsNotNone(carried, "改题干生成新版本后，原有正式标签必须自动继承")
        assert carried is not None
        self.assertEqual(carried.created_at, old_time)
        self.assertIn("不会", library.publication_json(pub_v2)["tags"])

    def test_undo_assign_preserves_original_created_at(self):
        _, pub = self._make_pub(3, "求函数 $y=\\ln x$ 的导数。")
        tag = self._make_tag("不会")
        old_time = timezone.now() - timedelta(days=200)
        self._attach_with_time(pub, tag, old_time)

        pub.refresh_from_db()
        res = library_tagging.assign(
            {
                "publication_ids": [str(pub.pk)],
                "add": [],
                "remove": [str(tag.pk)],
                "scopes": {},
                "expected_revisions": {str(pub.pk): pub.tag_revision},
                "request_id": str(uuid.uuid4()),
            }
        )
        self.assertFalse(LibraryTagAssignment.objects.filter(publication=pub, tag=tag).exists())

        undo_res = self.client.post(
            "/api/library/tags/undo",
            data=json.dumps({"operation_id": res["operation_id"]}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(undo_res.status_code, 200, undo_res.content)
        restored = LibraryTagAssignment.objects.get(publication=pub, tag=tag)
        self.assertEqual(restored.created_at, old_time)

    def test_duplicate_merge_keeps_original_and_earlier_tag_timestamps(self):
        paper_b = self._make_paper("2026周测手写错卷.png")
        _, keep_pub = self._make_pub(4, "设集合 $A=\\{1,2\\}$，求 $A$ 的子集个数。")
        _, victim_pub = self._make_pub(4, "设集合 $A=\\{1,2\\}$，求 $A$ 的子集个数。", paper=paper_b)
        tag_shared = self._make_tag("不会")
        tag_only_victim = self._make_tag("集合")

        newer_time = timezone.now() - timedelta(days=10)
        older_time = timezone.now() - timedelta(days=500)
        victim_only_time = timezone.now() - timedelta(days=300)

        self._attach_with_time(keep_pub, tag_shared, newer_time)
        self._attach_with_time(victim_pub, tag_shared, older_time)
        self._attach_with_time(victim_pub, tag_only_victim, victim_only_time)

        library_similarity.merge_library_publications(str(keep_pub.pk), [str(victim_pub.pk)])

        shared_row = LibraryTagAssignment.objects.get(publication=keep_pub, tag=tag_shared)
        adopted_row = LibraryTagAssignment.objects.get(publication=keep_pub, tag=tag_only_victim)
        self.assertEqual(shared_row.created_at, older_time)
        self.assertEqual(adopted_row.created_at, victim_only_time)

    def test_tag_merge_preserves_earliest_created_at(self):
        _, pub = self._make_pub(5, "解不等式 $x^2 - 1 > 0$。")
        src_tag = self._make_tag("旧不会")
        dst_tag = self._make_tag("不会")
        old_time = timezone.now() - timedelta(days=600)
        new_time = timezone.now() - timedelta(days=15)
        self._attach_with_time(pub, src_tag, old_time)
        self._attach_with_time(pub, dst_tag, new_time)

        impact_res = self.client.get(
            f"/api/library/tags/impact?action=merge_tags_batch&source_ids={src_tag.pk}&target_id={dst_tag.pk}",
            HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(impact_res.status_code, 200, impact_res.content)
        impact = impact_res.json()
        library_tagging.catalogue_mutation(
            {
                "action": "merge_tags_batch",
                "source_ids": [str(src_tag.pk)],
                "target_id": str(dst_tag.pk),
                "impact_token": impact["token"],
                "confirmed": True,
                "revision": impact["revision"],
                "request_id": str(uuid.uuid4()),
            }
        )
        merged_row = LibraryTagAssignment.objects.get(publication=pub, tag=dst_tag)
        self.assertEqual(merged_row.created_at, old_time)

    def test_browse_tag_age_filter_and_tagged_time_sorting(self):
        _, pub_recent = self._make_pub(10, "最近打标的题")
        _, pub_mid = self._make_pub(11, "四十天前打标的题")
        _, pub_ancient = self._make_pub(12, "两年前打标的题")
        _, pub_untagged = self._make_pub(13, "未打标的题")
        tag_hard = self._make_tag("不会")

        now = timezone.now()
        self._attach_with_time(pub_recent, tag_hard, now - timedelta(days=3))
        self._attach_with_time(pub_mid, tag_hard, now - timedelta(days=45))
        self._attach_with_time(pub_ancient, tag_hard, now - timedelta(days=730))

        # tag_age filter combined with tag_all
        within_7 = list(
            library_browse.library_rows({"tag_all": str(tag_hard.pk), "tag_age": "within_7d"}).values_list(
                "id", flat=True
            )
        )
        self.assertEqual(within_7, [pub_recent.pk])

        older_30 = set(
            library_browse.library_rows({"tag_all": str(tag_hard.pk), "tag_age": "older_30d"}).values_list(
                "id", flat=True
            )
        )
        self.assertEqual(older_30, {pub_mid.pk, pub_ancient.pk})

        older_365 = list(library_browse.library_rows({"tag_age": "older_365d"}).values_list("id", flat=True))
        self.assertEqual(older_365, [pub_ancient.pk])

        # Server sort: tagged_asc puts 2-year-old first, untagged last
        res_asc = self.client.get(
            f"/api/library?bulk=1&limit=20&offset=0&slim=1&sort=tagged_asc&tag_all={tag_hard.pk}", HTTP_X_QB_REQUEST="1"
        )
        self.assertEqual(res_asc.status_code, 200)
        asc_ids = [item["id"] for item in res_asc.json()["items"]]
        self.assertEqual(asc_ids, [str(pub_ancient.pk), str(pub_mid.pk), str(pub_recent.pk)])

        res_desc = self.client.get(
            "/api/library?bulk=1&limit=20&offset=0&slim=1&sort=tagged_desc", HTTP_X_QB_REQUEST="1"
        )
        self.assertEqual(res_desc.status_code, 200)
        desc_ids = [item["id"] for item in res_desc.json()["items"]]
        self.assertEqual(desc_ids[:3], [str(pub_recent.pk), str(pub_mid.pk), str(pub_ancient.pk)])
        self.assertEqual(desc_ids[-1], str(pub_untagged.pk))

    def test_backup_export_preserves_tag_assignment_created_at(self):
        _, pub = self._make_pub(15, "备份保留打标时间测试题")
        tag = self._make_tag("不会")
        old_time = timezone.now() - timedelta(days=410)
        self._attach_with_time(pub, tag, old_time)

        out_dir = self.root / "backup-out"
        out_dir.mkdir(parents=True, exist_ok=True)
        receipt = library_transfer.export_bundle(out_dir)
        zip_path = Path(receipt["file"])
        self.assertTrue(zip_path.is_file())
        # Verify the snapshot SQLite inside the zip preserves exact created_at
        import sqlite3
        import zipfile

        with zipfile.ZipFile(zip_path, "r") as zf:
            db_name = next(name for name in zf.namelist() if name.endswith(".sqlite3"))
            db_bytes = zf.read(db_name)
        snap_db = self.root / "snap.sqlite3"
        snap_db.write_bytes(db_bytes)
        conn = sqlite3.connect(str(snap_db))
        try:
            row = conn.execute("SELECT created_at FROM core_librarytagassignment LIMIT 1").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertTrue(str(row[0]).startswith(old_time.strftime("%Y-%m-%d")))

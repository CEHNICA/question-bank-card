"""Offline continuation: CAS, true pipeline ownership, and original-page safety."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest import mock

import pymupdf as fitz
from django.test import TestCase
from django.utils import timezone

from . import continue_ai_cut, library, mineru, pipeline, readers
from .models import Block, ImportChunk, Paper, PublishedQuestion, Question, QuestionGroup, RegionRead
from . import test_manual_intake_review as review_tests
from . import test_direct_cut_reading as direct_tests


class ContinueAICutTests(TestCase):
    setUp = review_tests.ManualIntakeReviewTests.setUp
    paper = review_tests.ManualIntakeReviewTests.paper
    reading = direct_tests.DirectCutReadingTests.reading

    def post(self, paper, payload=None, configured=False):
        with mock.patch.object(readers, "configured", return_value=configured), \
                mock.patch.object(pipeline, "request_extract_file_from_pool") as cloud, \
                mock.patch("requests.sessions.Session.request", side_effect=AssertionError("No endpoint network")):
            result = self.client.post(f"/api/papers/{paper.pk}/continue-ai-cut",
                json.dumps(payload if payload is not None else {"revision": 0}),
                content_type="application/json", HTTP_X_QB_REQUEST="1")
        cloud.assert_not_called()
        return result

    def block(self, paper, seq=0, number=1, y=100):
        return Block.objects.create(paper=paper, seq=seq, type="text", page_idx=0,
            bbox=[80, y, 920, y + 40], text=f"{number}. Find x when x + 2 = 5.")

    def card(self, paper, number=1, **fields):
        defaults = dict(paper=paper, number=number, processing_mode="manual", body_mode="text",
            state="green", stem="Human question $x^2$", question_type="free_response", edited=True,
            start_source="manual", source_kind="manual", regions=[{"page_idx": 0, "bbox": [80, 90, 920, 250]}])
        return Question.objects.create(**{**defaults, **fields})

    def test_revision_and_optional_field_types_are_strict(self):
        paper = self.paper()
        for revision in (None, True, False, "0", -1, 0.0, [], {}):
            with self.subTest(revision=revision):
                self.assertEqual(self.post(paper, {"revision": revision}).status_code, 400)
        for extra in ({"allow_cloud": 1}, {"allow_cloud": "true"}, {"expected_mode": []},
                {"expected_mode": "unknown"}, {"answer_model": "paid"}):
            with self.subTest(extra=extra):
                self.assertEqual(self.post(paper, {"revision": 0, **extra}).status_code, 400)
        self.assertEqual(self.post(paper, {}).status_code, 400)
        self.assertEqual(Paper.objects.get(pk=paper.pk).processing_plan["revision"], 0)

    def test_endpoint_has_local_json_guard_and_method_contract(self):
        paper = self.paper()
        path = f"/api/papers/{paper.pk}/continue-ai-cut"
        self.assertEqual(self.client.get(path).status_code, 405)
        self.assertEqual(self.client.post(path, "{}", content_type="application/json").status_code, 403)
        self.assertEqual(self.client.post(path, "{}", content_type="text/plain", HTTP_X_QB_REQUEST="1").status_code, 415)
        self.assertEqual(self.client.post(path, "[]", content_type="application/json", HTTP_X_QB_REQUEST="1").status_code, 400)

    def test_existing_blocks_continue_without_token_and_leave_every_saved_row(self):
        for mode, status in (("manual", "ready"), ("native", "failed"), ("mineru", "failed")):
            with self.subTest(mode=mode, status=status):
                paper = self.paper(mode=mode, status=status)
                paper.processing_plan["pages"] = [{"page_idx": i, "mode": "manual", "note": "saved"} for i in range(2)]
                paper.processing_plan["auto_fallback"] = True
                paper.progress, paper.total = 4, 9
                paper.save()
                card = self.card(paper)
                group = QuestionGroup.objects.create(paper=paper, title="Human scope", metadata={"pages": [0, 1]})
                card.group = group; card.save()
                library.approve(card, now=timezone.now()); card.save()
                publication, _ = library.publish(card)
                done = RegionRead.objects.create(question=card, page_idx=0, bbox=[80, 90, 200, 200],
                    target="stem", status="done", text="saved text")
                block = self.block(paper)
                cancel = pipeline.paper_dir(paper) / mineru.CANCEL_FILE
                cancel.write_bytes(b"old manual cancellation")
                original = Path(paper.source_path).read_bytes()
                before = {"question": Question.all_objects.values().get(pk=card.pk),
                    "publication": PublishedQuestion.objects.values().get(pk=publication.pk),
                    "group": QuestionGroup.objects.values().get(pk=group.pk),
                    "region": RegionRead.objects.values().get(pk=done.pk), "block": Block.objects.values().get(pk=block.pk)}
                result = self.post(paper, {"revision": 0, "allow_cloud": False})
                self.assertEqual(result.status_code, 200, result.content)
                body = result.json()
                self.assertEqual(body["action"], "local_segmentation")
                self.assertTrue(body["changed"] and body["reuses_saved_parse"])
                self.assertFalse(body["requires_cloud"])
                paper.refresh_from_db()
                self.assertEqual((paper.status, paper.processing_plan["mode"], paper.processing_plan["revision"]), ("segmenting", "mineru", 1))
                self.assertFalse(paper.processing_plan["auto_fallback"])
                self.assertEqual(paper.processing_plan["continue_existing_question_ids"], [card.pk])
                self.assertEqual([row["mode"] for row in paper.processing_plan["pages"]], ["mineru", "mineru"])
                self.assertEqual((paper.progress, paper.total), (4, 9))
                self.assertFalse(cancel.exists())
                self.assertEqual(Path(paper.source_path).read_bytes(), original)
                self.assertEqual(before, {"question": Question.all_objects.values().get(pk=card.pk),
                    "publication": PublishedQuestion.objects.values().get(pk=publication.pk),
                    "group": QuestionGroup.objects.values().get(pk=group.pk),
                    "region": RegionRead.objects.values().get(pk=done.pk), "block": Block.objects.values().get(pk=block.pk)})

    def test_active_mineru_is_exact_noop_without_settings_or_cancel_changes(self):
        for status in continue_ai_cut.ACTIVE:
            with self.subTest(status=status):
                paper = self.paper(mode="mineru", status=status)
                cancel = pipeline.paper_dir(paper) / mineru.CANCEL_FILE
                cancel.write_bytes(b"do not clear")
                before = Paper.objects.values().get(pk=paper.pk)
                with mock.patch.object(readers, "configured", side_effect=AssertionError("No settings lookup")):
                    result = self.client.post(f"/api/papers/{paper.pk}/continue-ai-cut", '{"revision":0}',
                        content_type="application/json", HTTP_X_QB_REQUEST="1")
                self.assertEqual(result.status_code, 200, result.content)
                self.assertEqual((result.json()["changed"], result.json()["action"]), (False, "already_running"))
                self.assertEqual(Paper.objects.values().get(pk=paper.pk), before)
                self.assertEqual(cancel.read_bytes(), b"do not clear")

    def test_stale_cas_and_mode_are_conflicts_even_for_running_task(self):
        paper = self.paper(mode="mineru", status="parsing")
        paper.processing_plan["revision"] = 7; paper.save()
        for payload in ({"revision": 0}, {"revision": 7, "expected_mode": "manual"}):
            result = self.post(paper, payload)
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json()["reason"], "stale_revision")
            self.assertEqual(result.json()["paper"]["processing_plan"]["revision"], 7)

    def test_zip_presence_does_not_grant_cloud_authorization_or_claim_saved_batch(self):
        paper = self.paper()
        archive = pipeline.paper_dir(paper) / "mineru_result.zip"
        archive.write_bytes(b"invalid cache must not silently cause cloud upload")
        paper.zip_path = str(archive); paper.save()
        self.assertEqual(self.post(paper).json()["reason"], "cloud_authorization")
        self.assertEqual(self.post(paper, {"revision": 0, "allow_cloud": True}).json()["reason"], "missing_mineru")
        result = self.post(paper, {"revision": 0, "allow_cloud": True}, configured=True)
        self.assertEqual(result.status_code, 200, result.content)
        body = result.json()
        self.assertEqual(body["action"], "queued_mineru")
        self.assertTrue(body["requires_cloud"])
        self.assertFalse(body["reuses_saved_parse"])
        self.assertEqual(archive.read_bytes(), b"invalid cache must not silently cause cloud upload")
        self.assertNotIn("batch_id", body)

    def test_archived_grouping_and_manual_active_tasks_are_not_started(self):
        cases = [("archived", {"archived": True}), ("active_or_ambiguous", {"status": "needs_grouping"}),
            ("active_or_ambiguous", {"status": "reading"}),
            ("needs_grouping", {"structure": {"suggested_groups": [[0], [1]]}}),
            ("needs_grouping", {"structure": {"groups_need_rebuild": True}})]
        for reason, changes in cases:
            with self.subTest(reason=reason, changes=changes):
                paper = self.paper(); self.block(paper)
                for key, value in changes.items(): setattr(paper, key, value)
                paper.save()
                before = Paper.objects.values().get(pk=paper.pk)
                result = self.post(paper)
                self.assertEqual(result.status_code, 409, result.content)
                self.assertEqual(result.json()["reason"], reason)
                self.assertEqual(Paper.objects.values().get(pk=paper.pk), before)

    def test_pending_single_or_region_reads_are_preserved_and_block_continuation(self):
        for fields in ({"ocr_pending": True}, {"reread_requested": True}, {"state": "reading"}, {"region": True}):
            with self.subTest(fields=fields):
                paper = self.paper(); self.block(paper)
                card = self.card(paper, **{key: value for key, value in fields.items() if key != "region"})
                if fields.get("region"):
                    RegionRead.objects.create(question=card, page_idx=0, bbox=[50, 50, 100, 100], target="stem")
                before = Question.objects.values().get(pk=card.pk)
                result = self.post(paper)
                self.assertEqual(result.status_code, 409, result.content)
                self.assertEqual(result.json()["reason"], "active_read")
                self.assertEqual(Question.objects.values().get(pk=card.pk), before)

    def test_missing_corrupt_or_changed_original_does_not_resume_even_with_blocks(self):
        for damage in ("missing", "corrupt", "changed_geometry"):
            with self.subTest(damage=damage):
                paper = self.paper(); self.block(paper)
                source = Path(paper.source_path)
                if damage == "missing": source.unlink()
                elif damage == "corrupt": source.write_bytes(b"no PDF")
                else:
                    with fitz.open() as document:
                        document.new_page(width=300, height=400); source.write_bytes(document.tobytes())
                result = self.post(paper)
                self.assertEqual(result.status_code, 409, result.content)
                self.assertEqual(result.json()["reason"], "missing_source")
                self.assertEqual(Paper.objects.get(pk=paper.pk).processing_plan["revision"], 0)

    def test_same_geometry_changed_original_digest_is_rejected_and_late_change_is_fenced(self):
        paper = self.paper(); self.block(paper)
        original = Path(paper.source_path).read_bytes()
        paper.processing_plan["render_sha256"] = hashlib.sha256(original).hexdigest(); paper.save()
        with fitz.open() as document:
            for _ in range(2):
                page = document.new_page(width=595, height=842)
                page.insert_text((50, 100), "Entirely different original problem")
            changed = document.tobytes()
        Path(paper.source_path).write_bytes(changed)
        result = self.post(paper)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["reason"], "missing_source")
        Path(paper.source_path).write_bytes(original)
        old = self.card(paper)
        before = Question.objects.values().get(pk=old.pk)
        self.assertEqual(self.post(paper).status_code, 200)
        Path(paper.source_path).write_bytes(changed)
        paper.refresh_from_db()
        pipeline.process_paper(paper)
        paper.refresh_from_db()
        self.assertEqual(paper.status, "failed")
        self.assertIn("原卷", paper.error)
        self.assertEqual(Question.objects.values().get(pk=old.pk), before)

    def test_failed_cancel_removal_rolls_back_plan_and_keeps_original_flag(self):
        paper = self.paper(); self.block(paper)
        target = pipeline.paper_dir(paper) / mineru.CANCEL_FILE
        target.write_bytes(b"stop")
        fake = mock.Mock()
        fake.exists.return_value = True; fake.read_bytes.return_value = b"stop"
        fake.unlink.side_effect = PermissionError("busy")
        before = Paper.objects.values().get(pk=paper.pk)
        with mock.patch.object(continue_ai_cut, "_cancellation_file", return_value=fake):
            result = self.post(paper)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["reason"], "cancel_unavailable")
        self.assertEqual(Paper.objects.values().get(pk=paper.pk), before)
        self.assertEqual(target.read_bytes(), b"stop")

    def test_book_ambiguous_prior_groups_are_rejected_without_regrouping(self):
        paper = self.paper(); self.block(paper)
        paper.material_type = "book"; paper.save()
        for seq in range(2):
            QuestionGroup.objects.create(paper=paper, title=f"Human {seq}", sequence=seq, metadata={"pages": [seq]})
        before = list(paper.question_groups.values())
        result = self.post(paper)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["reason"], "ambiguous_book_groups")
        self.assertEqual(list(paper.question_groups.values()), before)

    def test_demo_cannot_continue_through_direct_api(self):
        paper = self.paper(); self.block(paper)
        paper.structure = {"demo": True}; paper.save()
        before = Paper.objects.values().get(pk=paper.pk)
        result = self.post(paper, {"revision": 0, "allow_cloud": True}, configured=True)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["reason"], "demo")
        self.assertEqual(Paper.objects.values().get(pk=paper.pk), before)

    def real_layout(self, *, book=False):
        with fitz.open() as document:
            page = document.new_page(width=595, height=842)
            page.insert_text((50, 100), "1. Find x when x + 2 = 5.")
            page.insert_text((50, 400), "2. Find y when y - 1 = 3.")
            paper = self.paper(data=document.tobytes())
        if book:
            paper.material_type = "book"; paper.save()
        group = QuestionGroup.objects.create(paper=paper, title="Keep human scope", metadata={"pages": [0]})
        for seq, number, y in ((10, 1, 110), (20, 2, 460)):
            row = self.block(paper, seq, number, y)
            if book:
                row.text = f"例{number} 求函数的最大值。"; row.save()
        return paper, group

    def test_real_segmentation_and_reading_only_create_missing_card_whole_rows_stay(self):
        paper, group = self.real_layout()
        old = self.card(paper, group=group, number=1, answer="printed answer", analysis="saved explanation",
            options={"A": "$\\frac{1}{2}$"}, content_revision=8,
            figures=[{"page_idx": 0, "bbox": [200, 100, 400, 220], "slot": "stem", "source": "manual"}])
        # An unmatched automatic row is also saved content, not grounds to flag
        # it, re-read it, change its group, or move it to the recycle bin.
        unmatched = self.card(paper, group=group, number=99, edited=False, processing_mode="auto", state="waiting",
            regions=[{"page_idx": 0, "bbox": [5, 5, 30, 30]}])
        library.approve(old, now=timezone.now()); old.save()
        publication, _ = library.publish(old)
        before = {row.pk: Question.all_objects.values().get(pk=row.pk) for row in (old, unmatched)}
        group_before = QuestionGroup.objects.values().get(pk=group.pk)
        published_before = PublishedQuestion.objects.values().get(pk=publication.pk)
        self.assertEqual(self.post(paper).status_code, 200)
        paper.refresh_from_db()
        with mock.patch.object(pipeline, "ThreadPoolExecutor", review_tests.ImmediateExecutor), \
                mock.patch.object(pipeline, "close_old_connections"), \
                mock.patch.object(pipeline, "_reader_parallelism", return_value=1), \
                mock.patch.object(readers, "assistant_mode", return_value=False), \
                mock.patch.object(pipeline, "read_card", side_effect=lambda snapshot, store: self.reading(
                    stem="New recognized second question", foreign_figures=[{"number": 99, "group_id": group.pk,
                        "page_idx": 0, "bbox": [100, 600, 250, 750]}])) as read, \
                mock.patch("requests.sessions.Session.request", side_effect=AssertionError("offline pipeline")):
            pipeline.process_paper(paper)
        paper.refresh_from_db()
        self.assertEqual(paper.status, "ready", paper.error)
        added = Question.objects.get(paper=paper, number=2)
        self.assertEqual((added.processing_mode, added.stem, added.state), ("auto", "New recognized second question", "green"))
        self.assertEqual(read.call_count, 1)
        self.assertEqual(read.call_args[0][0]["id"], added.pk)
        self.assertEqual(before, {pk: Question.all_objects.values().get(pk=pk) for pk in before})
        self.assertEqual(QuestionGroup.objects.values().get(pk=group.pk), group_before)
        self.assertEqual(PublishedQuestion.objects.values().get(pk=publication.pk), published_before)
        self.assertEqual(Question.all_objects.filter(paper=paper).count(), 3)

    def test_real_book_segmentation_keeps_existing_manual_scope_and_no_duplicate_crop(self):
        paper, group = self.real_layout(book=True)
        old = self.card(paper, group=group, number=1, regions=[{"page_idx": 0, "bbox": [60, 90, 950, 400]}])
        before = Question.objects.values().get(pk=old.pk)
        group_before = QuestionGroup.objects.values().get(pk=group.pk)
        self.assertEqual(self.post(paper).status_code, 200)
        paper.refresh_from_db()
        with mock.patch.object(pipeline, "_prospective_book_groups", side_effect=AssertionError("No regrouping")), \
                mock.patch("requests.sessions.Session.request", side_effect=AssertionError("offline segment")):
            pipeline.segment_paper(paper)
        self.assertEqual(Question.objects.values().get(pk=old.pk), before)
        self.assertEqual(QuestionGroup.objects.values().get(pk=group.pk), group_before)
        self.assertEqual(Question.objects.filter(paper=paper, number=1).count(), 1)
        self.assertEqual(Question.objects.filter(paper=paper, number=2).count(), 1)

    def test_photo_continuation_maps_blocks_without_reordering_or_writing_originals(self):
        paper = self.paper(mode="manual")
        old = self.card(paper)
        paper.photos = {"order": [1, 0], "mineru_order": [0, 1], "manual": False,
            "files": [{"name": "one"}, {"name": "two"}], "notes": ["saved original metadata"]}
        paper.processing_plan.update(revision=1, continue_revision=1, continue_preserve_existing=True,
            continue_existing_question_ids=[old.pk])
        paper.save()
        before = Paper.objects.values().get(pk=paper.pk)
        rendered_before = Path(paper.source_path).read_bytes()
        blocks = [{"seq": 0, "type": "text", "page_idx": 0, "bbox": [80, 100, 920, 140], "text": "2. x"}]
        with mock.patch.object(pipeline.photos, "arrange", side_effect=AssertionError("No ordering")), \
                mock.patch.object(pipeline.photos, "build_pdf", side_effect=AssertionError("No original write")):
            mapped = pipeline.arrange_photo_pages(paper, blocks, preserve_order=True)
            current_upload = pipeline.arrange_photo_pages(paper, blocks, preserve_order=True, parsed_order=[1, 0])
        self.assertEqual(mapped[0]["page_idx"], 1)
        self.assertEqual(current_upload[0]["page_idx"], 0)
        self.assertEqual(Paper.objects.values().get(pk=paper.pk), before)
        self.assertEqual(Path(paper.source_path).read_bytes(), rendered_before)

    def test_new_photo_parse_records_current_order_and_cached_retry_maps_the_same_way(self):
        paper = self.paper()
        self.card(paper)
        paper.render_path = paper.source_path
        paper.photos = {"order": [1, 0], "mineru_order": [0, 1], "manual": False,
            "files": [{"name": "one"}, {"name": "two"}], "notes": []}
        paper.save()
        original, pages, photos = Path(paper.source_path).read_bytes(), deepcopy(paper.pages), deepcopy(paper.photos)
        self.assertEqual(self.post(paper, {"revision": 0, "allow_cloud": True}, configured=True).status_code, 200)
        blocks = [{"seq": 0, "type": "text", "page_idx": 0, "bbox": [80, 100, 920, 140], "text": "2. x"}]
        def extract(_source, target, *args, **kwargs): target.write_bytes(b"valid mocked ZIP")
        with mock.patch.object(pipeline, "load_blocks", side_effect=lambda *args: deepcopy(blocks)), \
                mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=extract) as cloud, \
                mock.patch.object(pipeline, "_plan_structure", return_value=({}, False)), \
                mock.patch.object(pipeline.photos, "build_pdf", side_effect=AssertionError("No original write")), \
                mock.patch.object(pipeline.photos, "arrange", side_effect=AssertionError("No ordering")):
            paper.refresh_from_db(); pipeline.parse(paper, revision=1)
            self.assertEqual(Block.objects.get(paper=paper).page_idx, 0)
            paper.refresh_from_db()
            self.assertEqual(paper.processing_plan["continue_photo_archive"]["order"], [1, 0])
            Paper.objects.filter(pk=paper.pk).update(status="queued")
            paper.refresh_from_db(); pipeline.parse(paper, revision=1)
            self.assertEqual(Block.objects.get(paper=paper).page_idx, 0)
            self.assertEqual(cloud.call_count, 1)
        paper.refresh_from_db()
        self.assertEqual((paper.pages, paper.photos), (pages, photos))
        self.assertEqual(Path(paper.source_path).read_bytes(), original)

    def test_old_worker_revision_cannot_write_after_continuation_and_new_retry_is_not_additive(self):
        paper = self.paper(mode="mineru", status="failed"); self.block(paper)
        card = self.card(paper)
        stale = Paper.objects.get(pk=paper.pk)
        before = Question.objects.values().get(pk=card.pk)
        self.assertEqual(self.post(paper).status_code, 200)
        self.assertFalse(pipeline._set_if_plan_current(stale, 0, status="failed", error="late"))
        with self.assertRaises(mineru.MineruCancelled): pipeline.segment_paper(stale)
        with self.assertRaises(mineru.MineruCancelled): pipeline.read_questions(stale, [card], revision=0)
        self.assertEqual(Question.objects.values().get(pk=card.pk), before)
        paper.refresh_from_db()
        self.assertTrue(pipeline._preserve_continued_cards(paper))
        plan = deepcopy(paper.processing_plan); plan["revision"] += 1
        paper.processing_plan = plan
        self.assertFalse(pipeline._preserve_continued_cards(paper))

    def test_chunked_photo_parse_uses_current_render_order_and_preserves_saved_pages(self):
        paper = self.paper()
        self.card(paper)
        paper.render_path = paper.source_path
        paper.photos = {"order": [1, 0], "mineru_order": [0, 1], "manual": False,
            "files": [{"name": "one"}, {"name": "two"}], "notes": []}
        paper.save()
        chunk = ImportChunk.objects.create(paper=paper, sequence=1, source_page_start=1,
            source_page_end=2, page_map=[1, 2], sha256="a" * 64)
        photos, pages = deepcopy(paper.photos), deepcopy(paper.pages)
        chunk_before = ImportChunk.objects.values().get(pk=chunk.pk)
        self.assertEqual(self.post(paper, {"revision": 0, "allow_cloud": True}, configured=True).status_code, 200)
        blocks = [{"seq": 0, "type": "text", "page_idx": 0, "bbox": [80, 100, 920, 140], "text": "2. x"}]
        with mock.patch.object(pipeline, "_chunk_blocks", return_value=blocks), \
                mock.patch.object(pipeline, "_plan_structure", return_value=({}, False)), \
                mock.patch.object(pipeline.photos, "build_pdf", side_effect=AssertionError("No original write")), \
                mock.patch.object(pipeline.photos, "arrange", side_effect=AssertionError("No ordering")):
            paper.refresh_from_db(); pipeline.parse(paper, revision=1)
        self.assertEqual(Block.objects.get(paper=paper).page_idx, 0)
        paper.refresh_from_db()
        self.assertEqual((paper.photos, paper.pages), (photos, pages))
        self.assertEqual(ImportChunk.objects.values().get(pk=chunk.pk), chunk_before)

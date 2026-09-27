"""Regression coverage for grouped numbering, task lifecycle and photo splitting.

These tests deliberately use temporary DATA_ROOT folders.  They must never read
or mutate the user's live database or uploaded papers.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image

from . import photos, pipeline, segment
from .models import Block, Paper, PublishedQuestion, Question, QuestionGroup


PAGES = [
    {"page_idx": index, "width": 1000, "height": 1400}
    for index in range(4)
]


def _question_item(page_idx: int, bottom: int) -> dict:
    return {
        "number": 1,
        "section": "",
        "question_type": "free_response",
        "regions": [{"page_idx": page_idx, "bbox": [40, 80, 960, bottom]}],
        "start": {"source": "mineru"},
        "figure_candidates": [],
    }


class ImportStructureMigrationTests(TransactionTestCase):
    """The 0009 table changes must not rewrite reviewed or published content."""

    migrate_from = [("core", "0008_question_figure_review")]
    migrate_to = [("core", "0009_import_structure")]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldPaper = old_apps.get_model("core", "Paper")
        OldQuestion = old_apps.get_model("core", "Question")
        OldPublishedQuestion = old_apps.get_model("core", "PublishedQuestion")

        paper = OldPaper.objects.create(
            filename="迁移前人工终审.pdf",
            task_name="人工校订任务",
            kind="pdf",
            sha256="7" * 64,
            source_path="data/legacy/source.pdf",
        )
        approved_at = timezone.now().replace(microsecond=123456)
        figures = [{
            "slot": "stem",
            "page_idx": 3,
            "bbox": [120, 240, 680, 720],
            "source": "manual",
        }]
        figure_review = {
            "status": "manual_confirmed",
            "reason": "teacher_crop",
            "reviewed": True,
        }
        question = OldQuestion.objects.create(
            paper=paper,
            number=12,
            section="第二章",
            question_type="single_choice",
            regions=[{"page_idx": 3, "bbox": [60, 100, 900, 900]}],
            regions_auto=[{"page_idx": 3, "bbox": [70, 110, 880, 880]}],
            start_source="manual",
            figure_candidates=[{"page_idx": 3, "bbox": [100, 200, 700, 750]}],
            figures=figures,
            figure_review=figure_review,
            read_a={"stem": "机器初读"},
            read_b={"stem": "机器复读"},
            stem="人工校订后的题干",
            options={"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
            text_source="human",
            state="green",
            flags=["人工确认配图"],
            edited=True,
            approved=True,
            approved_at=approved_at,
            approved_content_hash="a" * 64,
            answer="B",
            analysis="人工填写的完整解析",
        )
        snapshot = {
            "stem": question.stem,
            "options": question.options,
            "figures": figures,
            "figure_review": figure_review,
            "answer": question.answer,
            "analysis": question.analysis,
            "approved_content_hash": question.approved_content_hash,
        }
        publication = OldPublishedQuestion.objects.create(
            question=question,
            paper=paper,
            source_filename=paper.filename,
            number=question.number,
            question_type=question.question_type,
            version=1,
            status="published",
            content=snapshot,
            content_hash="b" * 64,
            search_text="人工校订后的题干 B 人工填写的完整解析",
        )

        self.paper_id = paper.pk
        self.question_id = question.pk
        self.publication_id = publication.pk
        self.approved_at = approved_at
        self.figures = figures
        self.figure_review = figure_review
        self.snapshot = snapshot

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_reviewed_card_and_publication_snapshot_survive_0009(self):
        MigratedQuestion = self.apps.get_model("core", "Question")
        MigratedPublishedQuestion = self.apps.get_model("core", "PublishedQuestion")
        MigratedGroup = self.apps.get_model("core", "QuestionGroup")

        question = MigratedQuestion.objects.get(pk=self.question_id)
        publication = MigratedPublishedQuestion.objects.get(pk=self.publication_id)
        group = MigratedGroup.objects.get(paper_id=self.paper_id)

        self.assertTrue(question.edited)
        self.assertTrue(question.approved)
        self.assertEqual(question.approved_at, self.approved_at)
        self.assertEqual(question.approved_content_hash, "a" * 64)
        self.assertEqual(question.figures, self.figures)
        self.assertEqual(question.figure_review, self.figure_review)
        self.assertEqual(question.answer, "B")
        self.assertEqual(question.analysis, "人工填写的完整解析")
        self.assertEqual(question.stem, "人工校订后的题干")
        self.assertEqual(question.group_id, group.pk)
        self.assertIsNotNone(question.source_key)

        self.assertEqual(publication.question_id, self.question_id)
        self.assertEqual(publication.paper_id, self.paper_id)
        self.assertEqual(publication.content, self.snapshot)
        self.assertEqual(publication.content_hash, "b" * 64)
        self.assertEqual(publication.search_text, "人工校订后的题干 B 人工填写的完整解析")

        with connection.cursor() as cursor:
            cursor.execute("PRAGMA quick_check")
            self.assertEqual(cursor.fetchall(), [("ok",)])
            cursor.execute("PRAGMA foreign_key_check")
            self.assertEqual(cursor.fetchall(), [])


class GroupedQuestionTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(
            filename="练习册.pdf",
            kind="pdf",
            material_type=Paper.MaterialType.BOOK,
            sha256="a" * 64,
            pages=PAGES,
            status=Paper.Status.SEGMENTING,
        )
        self.first = QuestionGroup.objects.create(
            paper=self.paper,
            title="第一章",
            kind=QuestionGroup.Kind.CHAPTER,
            sequence=0,
            page_start=1,
            page_end=2,
            metadata={"pages": [0, 1]},
        )
        self.second = QuestionGroup.objects.create(
            paper=self.paper,
            title="第二章",
            kind=QuestionGroup.Kind.CHAPTER,
            sequence=1,
            page_start=3,
            page_end=4,
            metadata={"pages": [2, 3]},
        )
        for page_idx in range(4):
            Block.objects.create(
                paper=self.paper,
                seq=page_idx,
                type="text",
                page_idx=page_idx,
                bbox=[40, 80, 960, 200],
                text="1. 每章都有自己的第 1 题",
            )
        self.first_bottom = 300

    def _build_questions(self, _layout, _starts, blocks):
        page_idx = min(block["page_idx"] for block in blocks)
        bottom = self.first_bottom if page_idx == 0 else 300
        return [_question_item(page_idx, bottom)]

    def _segment(self):
        with (
            mock.patch.object(pipeline.segment, "analyse", return_value=(object(), [])),
            mock.patch.object(pipeline, "locate_missing", return_value=[]),
            mock.patch.object(pipeline.segment, "build_questions", side_effect=self._build_questions),
            mock.patch.object(pipeline.segment, "text_blocks_in", return_value=["same"]),
            mock.patch.object(pipeline.imaging, "trim_regions", side_effect=lambda regions, _load: regions),
        ):
            pipeline.segment_paper(self.paper)

    def test_repeated_numbers_in_different_groups_survive_resegmentation_independently(self):
        self._segment()
        first_card = Question.objects.get(paper=self.paper, group=self.first, number=1)
        second_card = Question.objects.get(paper=self.paper, group=self.second, number=1)
        self.assertNotEqual(first_card.pk, second_card.pk)
        self.assertNotEqual(first_card.source_key, second_card.source_key)

        Question.objects.filter(pk=first_card.pk).update(
            stem="第一章第 1 题", state=Question.State.GREEN, approved=True,
            approved_content_hash="1" * 64,
        )
        Question.objects.filter(pk=second_card.pk).update(
            stem="第二章第 1 题", state=Question.State.GREEN, approved=True,
            approved_content_hash="2" * 64,
        )
        first_key, second_key = first_card.source_key, second_card.source_key

        # Only the first group's crop changes.  The second group's same-numbered
        # card must not be mistaken for it, deleted, or reset for another read.
        self.first_bottom = 360
        self._segment()

        first_card.refresh_from_db()
        second_card.refresh_from_db()
        self.assertEqual(first_card.source_key, first_key)
        self.assertEqual(second_card.source_key, second_key)
        self.assertEqual(first_card.regions[0]["bbox"][3], 360)
        self.assertEqual(first_card.state, Question.State.WAITING)
        self.assertFalse(first_card.approved)
        self.assertEqual(second_card.regions[0]["bbox"][3], 300)
        self.assertEqual(second_card.stem, "第二章第 1 题")
        self.assertEqual(second_card.state, Question.State.GREEN)
        self.assertTrue(second_card.approved)
        self.assertEqual(Question.objects.filter(paper=self.paper, number=1).count(), 2)

    def test_foreign_figure_is_assigned_only_within_the_same_group(self):
        first_card = Question.objects.create(
            paper=self.paper, group=self.first, number=1, stem="如图，第一章",
            state=Question.State.GREEN,
        )
        second_card = Question.objects.create(
            paper=self.paper, group=self.second, number=1, stem="如图，第二章",
            state=Question.State.GREEN,
        )

        pipeline.assign_foreign_figures(self.paper, [{
            "group_id": self.second.pk,
            "number": 1,
            "page_idx": 2,
            "bbox": [200, 300, 500, 600],
        }])

        first_card.refresh_from_db()
        second_card.refresh_from_db()
        self.assertEqual(first_card.figures, [])
        self.assertEqual(len(second_card.figures), 1)
        self.assertEqual(second_card.figures[0]["page_idx"], 2)
        self.assertEqual(second_card.figures[0]["source"], "other")

    def test_foreign_figure_without_a_group_is_not_guessed_when_number_is_ambiguous(self):
        first_card = Question.objects.create(
            paper=self.paper, group=self.first, number=1, stem="如图，第一章",
            state=Question.State.GREEN,
        )
        second_card = Question.objects.create(
            paper=self.paper, group=self.second, number=1, stem="如图，第二章",
            state=Question.State.GREEN,
        )

        pipeline.assign_foreign_figures(self.paper, [{
            "number": 1,
            "page_idx": 1,
            "bbox": [200, 300, 500, 600],
        }])

        first_card.refresh_from_db()
        second_card.refresh_from_db()
        self.assertEqual(first_card.figures, [])
        self.assertEqual(second_card.figures, [])

    def test_group_page_range_is_either_complete_or_entirely_unknown(self):
        for sequence, fields in enumerate((
            {"page_start": 1, "page_end": None},
            {"page_start": None, "page_end": 3},
        ), start=10):
            with self.subTest(fields=fields), self.assertRaises(IntegrityError):
                with transaction.atomic():
                    QuestionGroup.objects.create(
                        paper=self.paper,
                        title="不完整页码范围",
                        sequence=sequence,
                        **fields,
                    )


class BookScopeDetectionTests(TestCase):
    def test_same_page_number_restart_keeps_every_question_in_separate_groups(self):
        paper = Paper.objects.create(
            filename="同页两组练习.pdf",
            kind="pdf",
            material_type=Paper.MaterialType.BOOK,
            sha256="9" * 64,
            pages=[{"page_idx": 0, "width": 1000, "height": 1400}],
            status=Paper.Status.SEGMENTING,
        )
        blocks = []
        for seq, (number, top) in enumerate(((1, 80), (2, 300), (1, 600), (2, 900))):
            item = {
                "seq": seq, "type": "text", "page_idx": 0,
                "bbox": [40, top, 960, top + 100], "text": f"{number}. 已知条件，求结果",
            }
            blocks.append(item)
            Block.objects.create(paper=paper, **item)

        scopes = segment.numbering_scopes(paper.pages, blocks)
        self.assertEqual(len(scopes), 2)
        self.assertEqual((scopes[0]["seq_end"], scopes[1]["seq_start"]), (1, 2))
        structure, needs_confirmation = pipeline._plan_structure(paper, blocks)
        self.assertFalse(needs_confirmation)  # 书籍中的题号重启会自动保留，而不是卡住任务。
        paper.structure = structure
        paper.save(update_fields=["structure"])

        with mock.patch.object(pipeline.imaging, "trim_regions", side_effect=lambda regions, _load: regions), \
                mock.patch.object(pipeline, "locate_missing", return_value=[]):
            pipeline.segment_paper(paper)

        groups = list(paper.question_groups.order_by("sequence"))
        self.assertEqual(len(groups), 2)
        self.assertEqual(Question.objects.filter(paper=paper).count(), 4)
        self.assertEqual(Question.objects.filter(paper=paper, number=1).count(), 2)
        self.assertEqual(Question.objects.filter(paper=paper, number=2).count(), 2)
        self.assertEqual(
            [Question.objects.filter(group=group).count() for group in groups], [2, 2],
        )

        # Both scopes share page 0, so page membership alone must not guess where
        # a manually recovered question belongs. The API requires and records an
        # explicit group choice in this ambiguous case.
        client = Client()
        payload = {
            "number": 3,
            "regions": [{"page_idx": 0, "bbox": [40, 820, 960, 960]}],
        }
        ambiguous = client.post(
            f"/api/papers/{paper.pk}/questions",
            data=json.dumps(payload), content_type="application/json", HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(ambiguous.status_code, 400, ambiguous.content)
        self.assertIn("明确选择题组", ambiguous.json()["error"])

        chosen = client.post(
            f"/api/papers/{paper.pk}/questions",
            data=json.dumps({**payload, "group_id": groups[1].pk}),
            content_type="application/json", HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(chosen.status_code, 201, chosen.content)
        recovered = Question.objects.get(pk=chosen.json()["question"]["id"])
        self.assertEqual(recovered.group_id, groups[1].pk)


class TaskLifecycleTests(TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)
        self.client = Client()

    def _paper(self, status: str) -> tuple[Paper, Path]:
        paper = Paper.objects.create(
            filename=f"{status}.pdf", kind="pdf", sha256=(status[0] * 64), status=status,
        )
        folder = self.temp / str(paper.pk)
        folder.mkdir()
        source = folder / "source.pdf"
        source.write_bytes(b"source paper")
        paper.source_path = str(source)
        paper.save(update_fields=["source_path"])
        return paper, folder

    def _post(self, path: str, body=None):
        return self.client.post(
            path,
            data=json.dumps(body or {}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )

    def _delete(self, paper: Paper):
        return self.client.delete(f"/api/papers/{paper.pk}", HTTP_X_QB_REQUEST="1")

    def test_unpublished_finished_or_actionable_tasks_can_be_deleted(self):
        for status in (Paper.Status.READY, Paper.Status.FAILED, Paper.Status.NEEDS_GROUPING):
            with self.subTest(status=status):
                paper, folder = self._paper(status)
                response = self._delete(paper)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertFalse(Paper.objects.filter(pk=paper.pk).exists())
                self.assertFalse(folder.exists())

    def test_active_task_cannot_be_deleted(self):
        paper, folder = self._paper(Paper.Status.SEGMENTING)
        response = self._delete(paper)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertTrue(Paper.objects.filter(pk=paper.pk).exists())
        self.assertTrue(folder.exists())

    def test_publication_history_blocks_physical_delete_but_allows_archive(self):
        paper, folder = self._paper(Paper.Status.READY)
        question = Question.objects.create(paper=paper, number=1, stem="已入库题目")
        publication = PublishedQuestion.objects.create(
            question=question,
            paper=paper,
            source_filename=paper.filename,
            number=1,
            question_type="free_response",
            version=1,
            content={"stem": "已入库题目"},
            content_hash="f" * 64,
        )

        blocked = self._delete(paper)
        self.assertEqual(blocked.status_code, 400, blocked.content)
        self.assertTrue(Paper.objects.filter(pk=paper.pk).exists())
        self.assertTrue(folder.exists())

        archived = self._post(f"/api/papers/{paper.pk}/archive")
        self.assertEqual(archived.status_code, 200, archived.content)
        paper.refresh_from_db()
        self.assertTrue(paper.archived)
        self.assertTrue(Question.objects.filter(pk=question.pk).exists())
        self.assertTrue(PublishedQuestion.objects.filter(pk=publication.pk).exists())
        self.assertTrue(folder.exists())

    def test_structure_confirmation_continues_with_separate_numbering_scopes(self):
        paper, _folder = self._paper(Paper.Status.NEEDS_GROUPING)
        paper.pages = PAGES[:2]
        paper.structure = {
            "suggested_groups": [[0], [1]],
            "suggested_scopes": [
                {"sequence": 0, "pages": [0], "seq_start": None, "seq_end": 2},
                {"sequence": 1, "pages": [1], "seq_start": 3, "seq_end": None},
            ],
        }
        paper.save(update_fields=["pages", "structure"])
        response = self._post(f"/api/papers/{paper.pk}/confirm-structure")
        self.assertEqual(response.status_code, 200, response.content)
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.SEGMENTING)
        self.assertTrue(paper.structure["confirmed"])
        self.assertEqual(paper.structure["confirmed_groups"], [[0], [1]])
        self.assertEqual(len(paper.structure["confirmed_scopes"]), 2)

class PhotoSplitTests(TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)
        self.client = Client()
        self.paper = Paper.objects.create(
            filename="两份试卷照片",
            kind="image",
            material_type=Paper.MaterialType.EXAM,
            sha256="c" * 64,
            status=Paper.Status.NEEDS_GROUPING,
            pages=PAGES,
            structure={"signals": ["repeated_question_numbers"]},
        )
        self.folder = self.temp / str(self.paper.pk)
        self.folder.mkdir()
        files = []
        for index in range(4):
            raw_name = f"photo_{index + 1:02d}.jpg"
            image = Image.new("RGB", (160, 220), (255 - index * 10, 255, 255))
            image.save(self.folder / raw_name, format="JPEG")
            image.save(photos.page_file(self.folder, index), format="JPEG")
            files.append({"name": f"第{index + 1}页.jpg", "file": raw_name, "taken": ""})
            Block.objects.create(
                paper=self.paper,
                seq=index,
                type="text",
                page_idx=index,
                bbox=[20, 20, 140, 80],
                text=f"{1 if index in (0, 2) else 2}. 题目",
            )
        info = {
            "files": files,
            "order": [0, 1, 2, 3],
            "mineru_order": [0, 1, 2, 3],
            "enhance": False,
            "manual": False,
            "notes": [],
        }
        render = self.folder / "pages.pdf"
        photos.build_pdf(self.folder, info, render)
        self.paper.photos = info
        self.paper.source_path = str(self.folder / files[0]["file"])
        self.paper.render_path = str(render)
        self.paper.save(update_fields=["photos", "source_path", "render_path"])

    def _split(self, groups):
        return self.client.post(
            f"/api/papers/{self.paper.pk}/split",
            data=json.dumps({"groups": groups}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )

    def _source_snapshot(self):
        return {
            path.name: path.read_bytes()
            for path in self.folder.iterdir()
            if path.is_file()
        }

    def test_split_rejects_missing_duplicate_or_non_integer_pages_without_mutation(self):
        snapshot = self._source_snapshot()
        for groups in (
            [[0, 1], [2]],
            [[0, 1], [1, 2, 3]],
            [[0, 1], [2, True, 3]],
        ):
            with self.subTest(groups=groups):
                response = self._split(groups)
                self.assertEqual(response.status_code, 400, response.content)
                self.paper.refresh_from_db()
                self.assertFalse(self.paper.archived)
                self.assertEqual(Paper.objects.count(), 1)
                self.assertEqual(self._source_snapshot(), snapshot)

    def test_valid_split_keeps_archived_source_and_bidirectional_trace(self):
        snapshot = self._source_snapshot()
        response = self._split([[0, 1], [2, 3]])
        self.assertEqual(response.status_code, 201, response.content)

        self.paper.refresh_from_db()
        self.assertTrue(self.paper.archived)
        self.assertEqual(self._source_snapshot(), snapshot)
        children = list(Paper.objects.exclude(pk=self.paper.pk).order_by("created_at", "id"))
        self.assertEqual(len(children), 2)
        self.assertEqual(sorted(len(child.pages) for child in children), [2, 2])

        child_ids = {str(child.pk) for child in children}
        self.assertEqual(set(self.paper.structure["split_children"]), child_ids)
        self.assertEqual(self.paper.structure["split_groups"], [[0, 1], [2, 3]])
        self.assertEqual(
            sorted(page for child in children for page in child.structure["source_pages"]),
            [1, 2, 3, 4],
        )
        for child in children:
            self.assertEqual(child.structure["split_from"], str(self.paper.pk))
            self.assertFalse(child.archived)
            self.assertTrue((self.temp / str(child.pk)).is_dir())

        blocked_delete = self.client.delete(
            f"/api/papers/{self.paper.pk}", HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(blocked_delete.status_code, 400, blocked_delete.content)
        self.assertIn("追溯", blocked_delete.json()["error"])
        self.assertTrue(self.folder.is_dir())

    def test_split_does_not_copy_a_card_when_any_figure_stays_in_another_child(self):
        group = QuestionGroup.objects.create(
            paper=self.paper, title="原卷", sequence=0, page_start=1, page_end=4,
            metadata={"pages": [0, 1, 2, 3]},
        )
        Question.objects.create(
            paper=self.paper, group=group, number=1,
            regions=[{"page_idx": 0, "bbox": [20, 20, 900, 300]}],
            regions_auto=[{"page_idx": 0, "bbox": [20, 20, 900, 300]}],
            figures=[{"page_idx": 2, "bbox": [100, 100, 500, 500], "slot": "stem", "source": "auto"}],
            figure_review={"status": "ok"}, stem="如图，求解", state=Question.State.GREEN,
        )
        response = self._split([[0, 1], [2, 3]])
        self.assertEqual(response.status_code, 201, response.content)
        children = Paper.objects.exclude(pk=self.paper.pk)
        self.assertEqual(sum(child.questions.count() for child in children), 0)

    def test_reorder_then_confirm_rebuilds_scopes_with_new_block_sequence(self):
        legacy_group = QuestionGroup.objects.create(
            paper=self.paper,
            title="原题组",
            sequence=0,
            page_start=1,
            page_end=4,
            metadata={"pages": [0, 1, 2, 3], "source_pages": [11, 12, 21, 22]},
        )
        self.paper.structure = {
            "source_pages": [11, 12, 21, 22],
            "suggested_groups": [[0, 1], [2, 3]],
        }
        self.paper.save(update_fields=["structure"])

        reordered = self.client.post(
            f"/api/papers/{self.paper.pk}/page-order",
            data=json.dumps({"order": [2, 3, 0, 1]}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(reordered.status_code, 200, reordered.content)
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.NEEDS_GROUPING)
        self.assertEqual(self.paper.structure["source_pages"], [21, 22, 11, 12])
        self.assertEqual(
            list(self.paper.blocks.order_by("page_idx", "seq").values_list("page_idx", "seq")),
            [(0, 0), (1, 1), (2, 2), (3, 3)],
        )
        scopes = self.paper.structure["suggested_scopes"]
        self.assertEqual((scopes[0]["seq_end"], scopes[1]["seq_start"]), (1, 2))
        legacy_group.refresh_from_db()
        self.assertEqual(legacy_group.metadata["source_pages"], [21, 22, 11, 12])

        confirmed = self.client.post(
            f"/api/papers/{self.paper.pk}/confirm-structure",
            data=json.dumps({}), content_type="application/json", HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.paper.refresh_from_db()
        self.assertTrue(self.paper.structure["groups_need_rebuild"])

        with mock.patch.object(pipeline.imaging, "trim_regions", side_effect=lambda regions, _load: regions), \
                mock.patch.object(pipeline, "locate_missing", return_value=[]):
            pipeline.segment_paper(self.paper)

        self.paper.refresh_from_db()
        groups = list(self.paper.question_groups.order_by("sequence", "id"))
        self.assertEqual(len(groups), 2)
        self.assertEqual([group.metadata["pages"] for group in groups], [[0, 1], [2, 3]])
        self.assertFalse(self.paper.structure["groups_need_rebuild"])
        self.assertEqual(Question.objects.filter(paper=self.paper).count(), 4)
        self.assertEqual(
            [Question.objects.filter(group=group).count() for group in groups], [2, 2],
        )


class PdfSplitTests(TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)
        self.client = Client()

    def test_pdf_is_split_locally_and_keeps_original_untouched(self):
        import pymupdf as fitz

        paper = Paper.objects.create(
            filename="两份试卷.pdf",
            kind="pdf",
            sha256="d" * 64,
            status=Paper.Status.NEEDS_GROUPING,
            pages=PAGES,
            structure={"suggested_groups": [[0, 1], [2, 3]]},
        )
        folder = self.temp / str(paper.pk)
        folder.mkdir()
        source = folder / "source.pdf"
        document = fitz.open()
        try:
            for page_number in range(4):
                page = document.new_page(width=700, height=990)
                page.insert_text((50, 80), f"source page {page_number + 1}")
            document.save(source)
        finally:
            document.close()
        paper.source_path = str(source)
        paper.render_path = str(source)
        paper.save(update_fields=["source_path", "render_path"])
        for index in range(4):
            Block.objects.create(
                paper=paper,
                seq=index,
                type="text",
                page_idx=index,
                bbox=[40, 40, 650, 180],
                text=f"{index % 2 + 1}. 题目",
            )
        original = source.read_bytes()

        response = self.client.post(
            f"/api/papers/{paper.pk}/split",
            data=json.dumps({"groups": [[0, 1], [2, 3]]}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )

        self.assertEqual(response.status_code, 201, response.content)
        paper.refresh_from_db()
        self.assertTrue(paper.archived)
        self.assertEqual(source.read_bytes(), original)
        children = list(Paper.objects.exclude(pk=paper.pk).order_by("created_at", "id"))
        self.assertEqual(len(children), 2)
        for child in children:
            self.assertEqual(child.kind, "pdf")
            self.assertEqual(len(child.pages), 2)
            self.assertEqual(child.blocks.count(), 2)
            with fitz.open(child.source_path) as sliced:
                self.assertEqual(len(sliced), 2)

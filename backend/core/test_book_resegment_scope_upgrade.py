import json
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from . import pipeline, segment
from .models import Block, Paper, Question, QuestionGroup


PAGES = [{"page_idx": 0, "width": 1000, "height": 1400}]


def block(seq, y, text):
    return {
        "seq": seq,
        "type": "text",
        "page_idx": 0,
        "bbox": [80, y, 920, y + 45],
        "text": text,
    }


class BookResegmentScopeUpgradeTests(TestCase):
    """Regression contract for upgrading legacy exam-style textbook scopes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        setting = override_settings(DATA_ROOT=Path(self.temp.name))
        setting.enable()
        self.addCleanup(setting.disable)

        self.rows = [
            block(0, 80, "例1 求函数的最大值。"),
            block(1, 150, "分析：先研究函数的单调性。"),
            block(2, 220, "解：结果为 1。"),
            block(3, 330, "练习"),
            block(4, 400, "1. 求函数的最小值。"),
            block(5, 530, "例2 求函数的定义域。"),
            block(6, 610, "解：结果略。"),
        ]
        self.paper = Paper.objects.create(
            filename="旧版教材任务.pdf",
            kind="pdf",
            material_type=Paper.MaterialType.BOOK,
            sha256="c" * 64,
            source_path=str(Path(self.temp.name) / "missing-source.pdf"),
            pages=PAGES,
            status=Paper.Status.READY,
            # This is the shape persisted by the former exam-style grouping:
            # only the middle numbered exercise was made visible to segmentation.
            structure={
                "suggested_groups": [[0]],
                "suggested_scopes": [{
                    "pages": [0], "seq_start": 3, "seq_end": 4,
                    "first_number": 1, "start_page": 0,
                }],
            },
        )
        Block.objects.bulk_create([Block(paper=self.paper, **row) for row in self.rows])
        self.legacy_group = QuestionGroup.objects.create(
            paper=self.paper,
            title="旧版第 1 组",
            kind=QuestionGroup.Kind.EXAM,
            sequence=0,
            page_start=1,
            page_end=1,
            metadata={"pages": [0], "seq_start": 3, "seq_end": 4},
        )

        global_items = segment.segment_book(PAGES, self.rows)["questions"]
        self.assertEqual(
            [(item["source_kind"], item["source_anchor_seq"]) for item in global_items],
            [
                (Question.SourceKind.EXAMPLE, 0),
                (Question.SourceKind.EXERCISE, 4),
                (Question.SourceKind.EXAMPLE, 5),
            ],
        )
        exercise = next(item for item in global_items if item["source_anchor_seq"] == 4)
        approved_at = timezone.now()
        self.legacy_question = Question.objects.create(
            paper=self.paper,
            group=self.legacy_group,
            number=1,
            stem="人工核对后的练习题面",
            options={"A": "人工选项"},
            regions=exercise["regions"],
            regions_auto=exercise["regions"],
            start_source="mineru",
            # Migrated legacy cards did not yet know their typed marker, but the
            # global MinerU sequence already supplied a stable source identity.
            source_kind=Question.SourceKind.UNKNOWN,
            source_anchor_seq=4,
            read_a={"stem": "旧识读甲"},
            read_b={"stem": "旧识读乙"},
            text_source="human",
            edited=True,
            state=Question.State.GREEN,
            approved=True,
            approved_at=approved_at,
            approved_content_hash="a" * 64,
        )

    def post(self, path):
        return self.client.post(
            path,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )

    @staticmethod
    def desired_rows(report):
        rows = []
        for category in ("added", "kept", "range_changed", "too_long"):
            rows.extend(report["items"][category])
        return rows

    def test_preview_uses_all_book_blocks_instead_of_legacy_group_seq_bounds(self):
        groups_before = list(
            self.paper.question_groups.order_by("id").values(
                "id", "title", "kind", "sequence", "page_start", "page_end", "metadata",
            )
        )
        structure_before = deepcopy(self.paper.structure)
        question_before = Question.objects.values().get(pk=self.legacy_question.pk)

        with mock.patch.object(
            pipeline.imaging, "trim_regions", side_effect=lambda value, _load: value,
        ):
            response = self.post(f"/api/papers/{self.paper.pk}/resegment/preview")

        self.assertEqual(response.status_code, 200, response.content)
        report = response.json()["report"]
        self.assertTrue(report["read_only"])
        self.assertEqual(report["model_calls"], 0)
        desired = self.desired_rows(report)
        self.assertEqual(
            {(row["source_kind"], row["source_anchor_seq"]) for row in desired},
            {
                (Question.SourceKind.EXAMPLE, 0),
                (Question.SourceKind.EXERCISE, 4),
                (Question.SourceKind.EXAMPLE, 5),
            },
        )
        matched = [row for row in desired if row["source_anchor_seq"] == 4]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["question_id"], self.legacy_question.pk)

        self.paper.refresh_from_db()
        self.assertEqual(self.paper.structure, structure_before)
        self.assertEqual(
            list(self.paper.question_groups.order_by("id").values(
                "id", "title", "kind", "sequence", "page_start", "page_end", "metadata",
            )),
            groups_before,
        )
        self.assertEqual(
            Question.objects.values().get(pk=self.legacy_question.pk),
            question_before,
        )

    def test_source_anchor_matching_is_global_across_a_group_rebuild(self):
        replacement_group = QuestionGroup.objects.create(
            paper=self.paper,
            title="新版练习组",
            kind=QuestionGroup.Kind.CHAPTER,
            sequence=1,
            page_start=1,
            page_end=1,
            metadata={"pages": [0], "seq_start": 3, "seq_end": 4},
        )
        desired = [{
            "number": 1,
            "group": replacement_group,
            "regions": self.legacy_question.regions,
            "source_kind": Question.SourceKind.EXERCISE,
            "source_anchor_seq": 4,
        }]

        pairs, unmatched = pipeline._match_segmentation_items(
            [self.legacy_question], desired, [replacement_group],
        )

        self.assertEqual(pairs[0][1], self.legacy_question)
        self.assertEqual(unmatched, [])

    def test_apply_rebuilds_book_groups_and_preserves_the_matched_human_card(self):
        source_key = self.legacy_question.source_key
        approved_at = self.legacy_question.approved_at
        response = self.post(f"/api/papers/{self.paper.pk}/resegment")
        self.assertEqual(response.status_code, 200, response.content)
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.SEGMENTING)

        with mock.patch.object(
            pipeline.imaging, "trim_regions", side_effect=lambda value, _load: value,
        ):
            pipeline.segment_paper(self.paper)

        groups = list(self.paper.question_groups.order_by("sequence", "id"))
        self.assertEqual(len(groups), 3)
        self.assertEqual([group.kind for group in groups], [QuestionGroup.Kind.CHAPTER] * 3)
        self.assertEqual(
            [
                (
                    group.metadata.get("seq_start"),
                    group.metadata.get("seq_end"),
                )
                for group in groups
            ],
            [(None, 2), (3, 4), (5, None)],
        )

        questions = list(Question.objects.filter(paper=self.paper).order_by("source_anchor_seq"))
        self.assertEqual(
            [(question.source_kind, question.source_anchor_seq) for question in questions],
            [
                (Question.SourceKind.EXAMPLE, 0),
                (Question.SourceKind.EXERCISE, 4),
                (Question.SourceKind.EXAMPLE, 5),
            ],
        )
        preserved = Question.objects.get(pk=self.legacy_question.pk)
        self.assertEqual(preserved.source_key, source_key)
        self.assertEqual(preserved.group.sequence, 1)
        self.assertEqual(preserved.source_kind, Question.SourceKind.EXERCISE)
        self.assertEqual(preserved.stem, "人工核对后的练习题面")
        self.assertEqual(preserved.options, {"A": "人工选项"})
        self.assertEqual(preserved.read_a, {"stem": "旧识读甲"})
        self.assertEqual(preserved.read_b, {"stem": "旧识读乙"})
        self.assertTrue(preserved.edited)
        self.assertEqual(preserved.text_source, "human")
        self.assertEqual(preserved.state, Question.State.GREEN)
        self.assertTrue(preserved.approved)
        self.assertEqual(preserved.approved_at, approved_at)
        self.assertEqual(preserved.approved_content_hash, "a" * 64)
        self.assertEqual(Question.objects.filter(source_anchor_seq=4).count(), 1)

    def test_changed_range_never_overwrites_an_approved_human_card(self):
        protected_range = [{"page_idx": 0, "bbox": [80, 400, 920, 470]}]
        self.legacy_question.regions = protected_range
        self.legacy_question.regions_auto = protected_range
        self.legacy_question.save(update_fields=["regions", "regions_auto", "updated_at"])

        with mock.patch.object(
            pipeline.imaging, "trim_regions", side_effect=lambda value, _load: value,
        ):
            preview = self.post(f"/api/papers/{self.paper.pk}/resegment/preview")
        self.assertEqual(preview.status_code, 200, preview.content)
        protected = preview.json()["report"]["items"]["protected_unmatched"]
        self.assertTrue(any(row["question_id"] == self.legacy_question.pk for row in protected))

        applied = self.post(f"/api/papers/{self.paper.pk}/resegment")
        self.assertEqual(applied.status_code, 200, applied.content)
        self.paper.refresh_from_db()
        with mock.patch.object(
            pipeline.imaging, "trim_regions", side_effect=lambda value, _load: value,
        ):
            pipeline.segment_paper(self.paper)

        self.legacy_question.refresh_from_db()
        self.assertEqual(self.legacy_question.regions, protected_range)
        self.assertEqual(self.legacy_question.stem, "人工核对后的练习题面")
        self.assertFalse(self.legacy_question.approved)
        self.assertEqual(self.legacy_question.state, Question.State.YELLOW)
        self.assertIn(pipeline.FLAG_RESEGMENT_RANGE_PROTECTED, self.legacy_question.flags)

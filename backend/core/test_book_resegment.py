import json
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

from . import pipeline, segment
from .models import Block, Paper, PublishedQuestion, Question, QuestionDeletionBatch, QuestionGroup


PAGES = [{"page_idx": 0, "width": 1000, "height": 1400}]


def block(seq, y, text):
    return {
        "seq": seq,
        "type": "text",
        "page_idx": 0,
        "bbox": [80, y, 920, y + 45],
        "text": text,
    }


class BookResegmentSafetyTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        override = override_settings(DATA_ROOT=Path(self.temp.name))
        override.enable()
        self.addCleanup(override.disable)
        self.paper = Paper.objects.create(
            filename="教材.pdf",
            kind="pdf",
            material_type=Paper.MaterialType.BOOK,
            sha256="b" * 64,
            source_path=str(Path(self.temp.name) / "missing-source.pdf"),
            pages=PAGES,
            status=Paper.Status.READY,
        )
        self.group = QuestionGroup.objects.create(
            paper=self.paper,
            title="第一章",
            kind=QuestionGroup.Kind.CHAPTER,
            sequence=0,
            page_start=1,
            page_end=1,
            metadata={"pages": [0]},
        )

    def post(self, path, payload=None):
        return self.client.post(
            path,
            data=json.dumps(payload or {}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )

    def store_blocks(self, rows):
        Block.objects.bulk_create([Block(paper=self.paper, **row) for row in rows])

    def database_snapshot(self):
        return {
            "paper": list(Paper.objects.filter(pk=self.paper.pk).values()),
            "groups": list(QuestionGroup.objects.filter(paper=self.paper).values()),
            "blocks": list(Block.objects.filter(paper=self.paper).values()),
            "questions": list(Question.all_objects.filter(paper=self.paper).values()),
            "batches": list(QuestionDeletionBatch.objects.filter(paper=self.paper).values()),
            "publications": list(PublishedQuestion.objects.filter(paper=self.paper).values()),
        }

    def test_proof_instruction_is_kept_and_second_proof_heading_is_trimmed(self):
        task = (
            "证明：\n\n(1) $A=B$；\n\n(2) $C=D$.\n\n"
            "证明：(1) 由已知条件可得 $A=B$；\n\n(2) 同理可得 $C=D$."
        )

        trimmed, changed = pipeline._trim_example_solution_text(task)

        self.assertTrue(changed)
        self.assertEqual(trimmed, "证明：\n\n(1) $A=B$；\n\n(2) $C=D$.")
        instruction_only, changed = pipeline._trim_example_solution_text(
            "证明：\n\n(1) $A=B$；\n\n(2) $C=D$."
        )
        self.assertFalse(changed)
        self.assertEqual(instruction_only, "证明：\n\n(1) $A=B$；\n\n(2) $C=D$.")
        answer_only, changed = pipeline._trim_example_solution_text("解：只有印刷解答。")
        self.assertFalse(changed)
        self.assertEqual(answer_only, "解：只有印刷解答。")

    def test_preview_is_zero_write_and_never_calls_a_model(self):
        rows = [
            block(10, 80, "例1 求函数的最大值。"),
            block(11, 180, "解：计算可得。"),
            block(20, 380, "例2 求函数的最小值。"),
        ]
        self.store_blocks(rows)
        first = segment.segment_book(PAGES, rows)["questions"][0]
        Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=1,
            stem="例1题面",
            regions=first["regions"],
            regions_auto=first["regions"],
            source_kind=Question.SourceKind.EXAMPLE,
            source_anchor_seq=10,
            state=Question.State.GREEN,
        )
        before = deepcopy(self.database_snapshot())

        with mock.patch.object(pipeline.imaging, "trim_regions", side_effect=lambda value, _load: value), \
                mock.patch.object(pipeline, "locate_missing", side_effect=AssertionError("dry-run called AI")):
            response = self.post(f"/api/papers/{self.paper.pk}/resegment/preview")

        self.assertEqual(response.status_code, 200, response.content)
        report = response.json()["report"]
        self.assertTrue(report["read_only"])
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["summary"]["kept"], 1)
        self.assertEqual(report["summary"]["added"], 1)
        self.assertEqual(self.database_snapshot(), before)

    def test_solution_only_shortening_updates_saved_example_without_rereading(self):
        rows = [
            block(10, 80, "例1 如图，求函数的最大值。"),
            block(11, 220, "分析：先观察图象。\n解：计算可得。"),
            block(20, 400, "例2 求函数的最小值。"),
        ]
        old_regions = [{"page_idx": 0, "bbox": [80, 70, 920, 360]}]
        new_regions = [{"page_idx": 0, "bbox": [80, 70, 920, 200]}]
        kept_candidate = {"label": "1", "seq": 101, "page_idx": 0,
                          "bbox": [300, 120, 500, 180]}
        removed_candidate = {"label": "2", "seq": 102, "page_idx": 0,
                             "bbox": [300, 260, 500, 320]}
        reading = {
            "stem": "例1 如图，求函数的最大值。 **解：**先观察图象，计算可得。",
            "options": {}, "type": "free_response", "content_kind": "example",
            "figures": {"1": "stem", "2": "stem"}, "missing_figure": False,
            "figure_descriptions": ["stem"], "unclear": False,
        }
        question = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=1,
            stem=reading["stem"],
            read_a=reading,
            read_b=deepcopy(reading),
            text_source="agree",
            regions=old_regions,
            regions_auto=old_regions,
            figure_candidates=[kept_candidate, removed_candidate],
            figures=[
                {"slot": "stem", "page_idx": 0, "bbox": kept_candidate["bbox"], "source": "auto"},
                {"slot": "stem", "page_idx": 0, "bbox": removed_candidate["bbox"], "source": "auto"},
            ],
            source_kind=Question.SourceKind.EXAMPLE,
            source_anchor_seq=10,
            state=Question.State.YELLOW,
            flags=["两次识读不一致，已由第三次识读裁决"],
        )
        desired = [{
            "number": 1,
            "group": self.group,
            "section": "第一章",
            "question_type": "free_response",
            "regions": new_regions,
            "figure_candidates": [{key: value for key, value in kept_candidate.items() if key != "label"}],
            "segmentation_flags": [],
            "segmentation": {"solution_trimmed": True, "solution_boundary_seq": 11},
            "source_kind": Question.SourceKind.EXAMPLE,
            "source_anchor_seq": 10,
            "start": {"source": "mineru"},
        }]
        self.paper.status = Paper.Status.SEGMENTING
        self.paper.save(update_fields=["status"])

        with mock.patch.object(
                pipeline, "_prospective_book_groups",
                return_value=([self.group], dict(self.paper.structure or {}))), \
                mock.patch.object(pipeline, "_ensure_question_groups", return_value=[self.group]), \
                mock.patch.object(
                    pipeline, "_collect_segmentation_items",
                    return_value=(rows, desired, [], []),
                ):
            preview = pipeline.preview_resegment(self.paper)
            self.assertEqual(preview["summary"]["locally_trimmed"], 1)
            self.assertEqual(preview["summary"]["range_changed"], 0)
            pipeline.segment_paper(self.paper)

        question.refresh_from_db()
        self.paper.refresh_from_db()
        self.assertEqual(question.state, Question.State.GREEN)
        self.assertEqual(question.regions, new_regions)
        self.assertEqual(question.stem, "例1 如图，求函数的最大值。")
        self.assertEqual(question.read_a["stem"], question.stem)
        self.assertEqual(question.read_b["stem"], question.stem)
        self.assertEqual(question.read_a["figures"], {"1": "stem"})
        self.assertEqual(len(question.figure_candidates), 1)
        self.assertEqual(len(question.figures), 1)
        self.assertFalse(question.reread_requested)
        self.assertTrue(any("不调用模型" in note for note in self.paper.notes))

        # The narrow maintenance path performs the same proven migration even
        # when a full re-segmentation is intentionally unavailable (for
        # example, while recoverable cards remain in the recycle bin).
        question.regions = old_regions
        question.regions_auto = old_regions
        question.stem = reading["stem"]
        question.read_a = reading
        question.read_b = deepcopy(reading)
        question.figure_candidates = [kept_candidate, removed_candidate]
        question.figures = [
            {"slot": "stem", "page_idx": 0, "bbox": kept_candidate["bbox"], "source": "auto"},
            {"slot": "stem", "page_idx": 0, "bbox": removed_candidate["bbox"], "source": "auto"},
        ]
        question.figure_review = {}
        question.state = Question.State.YELLOW
        question.flags = []
        question.save()
        with mock.patch.object(
                pipeline, "_collect_segmentation_items",
                return_value=(rows, desired, [], [])):
            counts = pipeline.trim_book_example_solutions_locally(self.paper)
        question.refresh_from_db()
        self.assertEqual(counts, {
            "found": 1, "trimmed": 1, "unchanged": 0, "protected": 0, "unsafe": 0,
        })
        self.assertEqual(question.stem, "例1 如图，求函数的最大值。")
        self.assertEqual(question.regions, new_regions)
        with mock.patch.object(
                pipeline, "_collect_segmentation_items",
                return_value=(rows, desired, [], [])):
            repeated = pipeline.trim_book_example_solutions_locally(self.paper)
        self.assertEqual(repeated, {
            "found": 1, "trimmed": 0, "unchanged": 1, "protected": 0, "unsafe": 0,
        })

    def test_local_trim_can_add_numbered_input_visual_without_rereading(self):
        rows = [
            block(10, 80, "例4 如图 4.2-7，估计城市人口的倍增期。"),
            block(11, 220, "分析：先观察图象。"),
            {"seq": 12, "type": "chart", "page_idx": 0,
             "bbox": [600, 300, 900, 510], "text": ""},
            block(20, 620, "例5 求函数的定义域。"),
        ]
        core_regions = [{"page_idx": 0, "bbox": [80, 70, 920, 200]}]
        recovered_region = {"page_idx": 0, "bbox": [600, 300, 900, 510]}
        reading = {
            "stem": "例4 如图 4.2-7，估计城市人口的倍增期。",
            "options": {}, "type": "free_response", "content_kind": "example",
            "figures": {}, "missing_figure": True,
            "figure_descriptions": ["stem"], "unclear": False,
        }
        question = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=4,
            stem=reading["stem"],
            read_a=reading,
            read_b=deepcopy(reading),
            text_source="agree",
            regions=core_regions,
            regions_auto=core_regions,
            figure_candidates=[],
            figures=[],
            source_kind=Question.SourceKind.EXAMPLE,
            source_anchor_seq=10,
            state=Question.State.YELLOW,
            flags=["题目文字提示有图，但没有可用配图"],
        )
        desired = [{
            "number": 4,
            "group": self.group,
            "section": "第一章",
            "question_type": "free_response",
            "regions": [*core_regions, recovered_region],
            "figure_candidates": [{
                "seq": 12, **recovered_region, "recovered_input": True,
            }],
            "segmentation_flags": [],
            "segmentation": {
                "solution_trimmed": True,
                "solution_boundary_seq": 11,
                "recovered_input_figure_seqs": [12],
            },
            "source_kind": Question.SourceKind.EXAMPLE,
            "source_anchor_seq": 10,
            "start": {"source": "mineru"},
        }]

        with mock.patch.object(
                pipeline, "_collect_segmentation_items",
                return_value=(rows, desired, [], [])):
            counts = pipeline.trim_book_example_solutions_locally(self.paper)

        question.refresh_from_db()
        self.assertEqual(counts, {
            "found": 1, "trimmed": 1, "unchanged": 0, "protected": 0, "unsafe": 0,
        })
        self.assertEqual(question.regions, [*core_regions, recovered_region])
        self.assertEqual(question.figure_candidates[0]["recovered_input"], True)
        self.assertEqual(question.read_a["figures"], {"1": "stem"})
        self.assertEqual(question.read_b["figures"], {"1": "stem"})
        self.assertEqual(question.figures, [{
            "slot": "stem", "page_idx": 0,
            "bbox": recovered_region["bbox"], "source": "auto",
        }])
        self.assertFalse(question.reread_requested)

    def test_changed_exercise_still_waits_for_a_fresh_read(self):
        rows = [block(10, 80, "1. 求函数的最大值。"), block(11, 220, "补充条件。")]
        old_regions = [{"page_idx": 0, "bbox": [80, 70, 920, 180]}]
        new_regions = [{"page_idx": 0, "bbox": [80, 70, 920, 280]}]
        question = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=1,
            stem="求函数的最大值。",
            regions=old_regions,
            regions_auto=old_regions,
            source_kind=Question.SourceKind.EXERCISE,
            source_anchor_seq=10,
            state=Question.State.GREEN,
        )
        desired = [{
            "number": 1, "group": self.group, "section": "第一章",
            "question_type": "free_response", "regions": new_regions,
            "figure_candidates": [], "segmentation_flags": [], "segmentation": {},
            "source_kind": Question.SourceKind.EXERCISE, "source_anchor_seq": 10,
            "start": {"source": "mineru"},
        }]
        self.paper.status = Paper.Status.SEGMENTING
        self.paper.save(update_fields=["status"])
        with mock.patch.object(
                pipeline, "_prospective_book_groups",
                return_value=([self.group], dict(self.paper.structure or {}))), \
                mock.patch.object(pipeline, "_ensure_question_groups", return_value=[self.group]), \
                mock.patch.object(
                    pipeline, "_collect_segmentation_items",
                    return_value=(rows, desired, [], []),
                ):
            pipeline.segment_paper(self.paper)

        question.refresh_from_db()
        self.assertEqual(question.state, Question.State.WAITING)
        self.assertEqual(question.regions, new_regions)

    def test_unchanged_card_drops_an_automatic_figure_moved_by_caption_rule(self):
        rows = [block(10, 80, "8. 求函数的定义域。")]
        regions = [{"page_idx": 0, "bbox": [80, 70, 920, 300]}]
        old_candidate = {
            "label": "1", "seq": 11, "page_idx": 0,
            "bbox": [620, 180, 900, 290],
        }
        question = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=8,
            stem="求函数的定义域。",
            read_a={
                "stem": "求函数的定义域。", "options": {},
                "type": "free_response", "figures": {"1": "stem"},
                "missing_figure": False,
            },
            read_b={
                "stem": "求函数的定义域。", "options": {},
                "type": "free_response", "figures": {"1": "stem"},
                "missing_figure": False,
            },
            regions=regions,
            regions_auto=regions,
            figure_candidates=[old_candidate],
            figures=[{
                "slot": "stem", "page_idx": 0,
                "bbox": old_candidate["bbox"], "source": "auto",
            }],
            figure_review={"status": "conflict", "source": "automatic"},
            flags=[pipeline.FLAG_UNCUED_FIGURE],
            source_kind=Question.SourceKind.EXERCISE,
            source_anchor_seq=10,
            state=Question.State.YELLOW,
            approved=True,
        )
        desired = [{
            "number": 8, "group": self.group, "section": "第一章",
            "question_type": "free_response", "regions": regions,
            "figure_candidates": [], "segmentation_flags": [], "segmentation": {},
            "source_kind": Question.SourceKind.EXERCISE, "source_anchor_seq": 10,
            "start": {"source": "mineru"},
        }]

        with mock.patch.object(
                pipeline, "_prospective_book_groups",
                return_value=([self.group], dict(self.paper.structure or {}))), \
                mock.patch.object(pipeline, "_ensure_question_groups", return_value=[self.group]), \
                mock.patch.object(
                    pipeline, "_collect_segmentation_items",
                    return_value=(rows, desired, [], []),
                ):
            pipeline.segment_paper(self.paper)

        question.refresh_from_db()
        self.assertEqual(question.figure_candidates, [])
        self.assertEqual(question.figures, [])
        self.assertEqual(question.figure_review["status"], "ok")
        self.assertEqual(question.flags, [])
        self.assertEqual(question.state, Question.State.GREEN)
        self.assertFalse(question.approved)

    def test_apply_moves_unmatched_auto_card_to_recoverable_system_batch(self):
        rows = [block(20, 380, "例2 求函数的最小值。")]
        self.store_blocks(rows)
        orphan = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=1,
            stem="旧规则多切出的自动题卡",
            regions=[{"page_idx": 0, "bbox": [80, 80, 920, 220]}],
            regions_auto=[{"page_idx": 0, "bbox": [80, 80, 920, 220]}],
            source_kind=Question.SourceKind.EXAMPLE,
            source_anchor_seq=10,
            state=Question.State.GREEN,
        )
        human = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=99,
            stem="人工修订必须保留",
            edited=True,
            text_source="human",
            regions=[{"page_idx": 0, "bbox": [80, 800, 920, 900]}],
            regions_auto=[{"page_idx": 0, "bbox": [80, 800, 920, 900]}],
            source_kind=Question.SourceKind.UNKNOWN,
            state=Question.State.GREEN,
        )
        publication = PublishedQuestion.objects.create(
            id=uuid.uuid4(),
            question=human,
            paper=self.paper,
            source_filename=self.paper.display_name,
            number=99,
            question_type="unknown",
            version=1,
            content={"stem": human.stem},
            content_hash="c" * 64,
        )
        publication_content = deepcopy(publication.content)

        applied = self.post(f"/api/papers/{self.paper.pk}/resegment")
        self.assertEqual(applied.status_code, 200, applied.content)
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.SEGMENTING)
        with mock.patch.object(pipeline.imaging, "trim_regions", side_effect=lambda value, _load: value):
            pipeline.segment_paper(self.paper)

        self.assertFalse(Question.objects.filter(pk=orphan.pk).exists())
        deleted = Question.all_objects.get(pk=orphan.pk)
        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(deleted.deletion_batch.origin, QuestionDeletionBatch.Origin.RESEGMENT)
        self.assertIn(pipeline.FLAG_RESEGMENT_EXCLUDED, deleted.flags)
        human.refresh_from_db()
        self.assertEqual(human.stem, "人工修订必须保留")
        self.assertEqual(human.state, Question.State.YELLOW)
        publication.refresh_from_db()
        self.assertEqual(publication.content, publication_content)

        Paper.objects.filter(pk=self.paper.pk).update(status=Paper.Status.READY)
        restored = self.post(
            f"/api/papers/{self.paper.pk}/question-trash/{deleted.deletion_batch_id}/restore",
        )
        self.assertEqual(restored.status_code, 200, restored.content)
        self.assertTrue(Question.objects.filter(pk=orphan.pk).exists())
        self.assertEqual(Question.objects.get(pk=orphan.pk).state, Question.State.YELLOW)

    def test_repeated_example_one_matches_by_anchor_not_nearest_position(self):
        first_regions = [{"page_idx": 0, "bbox": [40, 100, 470, 200]}]
        second_regions = [{"page_idx": 0, "bbox": [40, 320, 470, 420]}]
        first = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=1,
            stem="第一处例1",
            regions=first_regions,
            regions_auto=first_regions,
            source_kind=Question.SourceKind.EXAMPLE,
            source_anchor_seq=10,
            state=Question.State.GREEN,
        )
        second = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=1,
            stem="第二处例1",
            regions=second_regions,
            regions_auto=second_regions,
            source_kind=Question.SourceKind.EXAMPLE,
            source_anchor_seq=20,
            state=Question.State.GREEN,
        )
        first_key, second_key = first.source_key, second.source_key
        desired = [
            {
                "number": 1, "group": self.group, "section": "第二位置",
                "question_type": "unknown", "regions": second_regions,
                "figure_candidates": [], "segmentation_flags": [],
                "source_kind": "example", "source_anchor_seq": 10,
                "start": {"source": "mineru"},
            },
            {
                "number": 1, "group": self.group, "section": "第一位置",
                "question_type": "unknown", "regions": first_regions,
                "figure_candidates": [], "segmentation_flags": [],
                "source_kind": "example", "source_anchor_seq": 20,
                "start": {"source": "mineru"},
            },
        ]
        self.paper.status = Paper.Status.SEGMENTING
        self.paper.save(update_fields=["status"])
        with mock.patch.object(
                pipeline, "_prospective_book_groups",
                return_value=([self.group], dict(self.paper.structure or {}))), \
                mock.patch.object(pipeline, "_ensure_question_groups", return_value=[self.group]), \
                mock.patch.object(
                    pipeline,
                    "_collect_segmentation_items",
                    return_value=([], desired, [], []),
                ):
            pipeline.segment_paper(self.paper)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.source_key, first_key)
        self.assertEqual(second.source_key, second_key)
        self.assertEqual(first.source_anchor_seq, 10)
        self.assertEqual(second.source_anchor_seq, 20)
        self.assertEqual(first.regions, second_regions)
        self.assertEqual(second.regions, first_regions)

    def test_new_typed_anchor_does_not_steal_a_different_existing_card_with_same_number(self):
        existing = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=1,
            stem="练习 1",
            regions=[{"page_idx": 0, "bbox": [40, 300, 470, 400]}],
            regions_auto=[{"page_idx": 0, "bbox": [40, 300, 470, 400]}],
            source_kind=Question.SourceKind.EXERCISE,
            source_anchor_seq=20,
            state=Question.State.GREEN,
        )
        desired = [{
            "number": 1,
            "group": self.group,
            "regions": [{"page_idx": 0, "bbox": [40, 100, 470, 200]}],
            "source_kind": Question.SourceKind.EXAMPLE,
            "source_anchor_seq": 10,
        }]

        pairs, unmatched = pipeline._match_segmentation_items([existing], desired, [self.group])

        self.assertIsNone(pairs[0][1])
        self.assertEqual(unmatched, [existing])

    def test_preview_and_apply_require_ready_and_empty_trash(self):
        self.store_blocks([block(10, 80, "例1 求值。")])
        self.paper.status = Paper.Status.FAILED
        self.paper.save(update_fields=["status"])
        for suffix in ("resegment/preview", "resegment"):
            self.assertEqual(
                self.post(f"/api/papers/{self.paper.pk}/{suffix}").status_code,
                409,
            )

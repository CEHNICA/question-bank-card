from django.test import SimpleTestCase, TestCase
from unittest.mock import patch

from . import pipeline, readers
from .models import Question
from .textnorm import canon


class ContentKindReadingTests(SimpleTestCase):
    def test_parser_keeps_example_kind_in_existing_reading_payload(self):
        result = readers.parse_reading(
            "【内容类型】例题\n【题号】6\n【题型】解答题\n"
            "【题干】求函数的定义域。",
            6,
        )

        self.assertEqual(result["content_kind"], "example")
        self.assertEqual(result["stem"], "求函数的定义域。")

    def test_legacy_reader_output_without_kind_remains_compatible(self):
        result = readers.parse_reading(
            "【题号】3\n【题型】解答题\n【题干】求 $x$。",
            3,
        )

        self.assertEqual(result["content_kind"], "unknown")

    def test_book_prompt_does_not_force_candidate_to_be_a_question(self):
        prompt = readers.transcribe_prompt(6, with_figures=False, source_kind="example")

        self.assertIn("独立判断候选内容的性质", prompt)
        self.assertIn("本地版面规则检测到", prompt)
        self.assertIn("【内容类型】", prompt)
        self.assertNotIn("这道题应当是第 6 题", prompt)

    def test_arbiter_must_return_content_kind_too(self):
        prompt = readers.arbiter_prompt(
            3,
            {"stem": "单调性", "options": {}, "content_kind": "heading"},
            {"stem": "单调性", "options": {}, "content_kind": "prose"},
        )

        self.assertIn("给出正确的誊录和内容类型", prompt)
        self.assertIn("【内容类型】", prompt)

    def test_superscript_and_latex_exponent_compare_equally(self):
        self.assertEqual(canon("面积为 25 cm²"), canon(r"面积为 $25\text{ cm}^2$"))

    def test_snapshot_keeps_only_deterministic_segmentation_warning(self):
        question = Question(
            id=7, number=1, regions=[], figure_candidates=[], options={},
            source_kind=Question.SourceKind.EXAMPLE, source_anchor_seq=22,
            flags=["书本切题范围超过 4 页，已在安全上限停止；请对照原书调整范围", "旧识读警告"],
        )

        snapshot = pipeline._snapshot(question)

        self.assertEqual(snapshot["source_kind"], Question.SourceKind.EXAMPLE)
        self.assertEqual(snapshot["source_anchor_seq"], 22)
        self.assertEqual(len(snapshot["segmentation_flags"]), 1)

    def test_explicit_example_anchor_is_kept_but_model_conflict_turns_yellow(self):
        reading = {
            "stem": "单调性", "options": {}, "type": "free_response",
            "content_kind": "prose", "figures": {}, "missing_figure": False, "number_seen": 1, "figure_descriptions": [], "unclear": False,
        }
        snapshot = {
            "id": 1, "number": 1, "group_id": None, "regions": [{"page_idx": 0, "bbox": [0, 0, 10, 10]}],
            "candidates": [], "question_type": "unknown", "stem": "", "options": {}, "edited": False,
            "source_kind": Question.SourceKind.EXAMPLE, "source_anchor_seq": 1, "segmentation_flags": [],
        }
        fake_store = type("Store", (), {"load": lambda self, page: object()})()
        with patch.object(pipeline.readers, "primary_engine", return_value=object()), \
                patch.object(pipeline.readers, "checker_engine", return_value=object()), \
                patch.object(pipeline.readers, "read_question", return_value=reading), \
                patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])), \
                patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:image/jpeg;base64,x"):
            result = pipeline.read_card(snapshot, fake_store)

        self.assertEqual(result["state"], Question.State.YELLOW)
        self.assertTrue(any("本地版面规则判为例题" in flag for flag in result["flags"]))

    def test_numbered_exploration_tasks_outweigh_coarse_prose_label(self):
        reading = {
            "stem": (
                "先说明命题的背景。\n"
                "(1) 请正确地写出两个命题的否定并判断真假。\n"
                "(2) 请列举两个例子并说明理由。"
            ),
            "options": {}, "type": "free_response",
            "content_kind": "prose", "figures": {}, "missing_figure": False, "number_seen": 6, "figure_descriptions": [], "unclear": False,
        }
        snapshot = {
            "id": 6, "number": 6, "group_id": None,
            "regions": [{"page_idx": 0, "bbox": [0, 0, 10, 10]}],
            "candidates": [], "question_type": "free_response", "stem": "", "options": {},
            "edited": False, "source_kind": Question.SourceKind.EXERCISE,
            "source_anchor_seq": 6, "segmentation_flags": [],
        }
        fake_store = type("Store", (), {"load": lambda self, page: object()})()
        with patch.object(pipeline.readers, "primary_engine", return_value=object()), \
                patch.object(pipeline.readers, "checker_engine", return_value=object()), \
                patch.object(pipeline.readers, "read_question", return_value=reading), \
                patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])), \
                patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:image/jpeg;base64,x"):
            result = pipeline.read_card(snapshot, fake_store)

        self.assertEqual(result["state"], Question.State.GREEN)
        self.assertFalse(any("本地版面规则判为" in flag for flag in result["flags"]))

    def test_content_votes_treat_example_and_exercise_as_question_kinds(self):
        self.assertTrue(pipeline._source_kind_has_question_support(
            expected_kind="exercise",
            stem="求函数的定义域。",
            options={},
            kind="free_response",
            readings=[
                {"content_kind": "exercise"},
                {"content_kind": "prose"},
                {"content_kind": "exercise"},
            ],
        ))
        self.assertTrue(pipeline._source_kind_has_question_support(
            expected_kind="exercise",
            stem="一个圆柱形容器，求液面高度。",
            options={},
            kind="free_response",
            readings=[{"content_kind": "example"}, {"content_kind": "exercise"}],
        ))

    def test_explicit_question_and_activity_outweigh_coarse_prose_votes(self):
        self.assertTrue(pipeline._source_kind_has_question_support(
            expected_kind="exercise",
            stem="反过来，已知碳 14 的含量，如何得知它死亡了多长时间呢？",
            options={},
            kind="free_response",
            readings=[{"content_kind": "prose"}, {"content_kind": "prose"}],
        ))
        self.assertTrue(pipeline._source_kind_has_question_support(
            expected_kind="exercise",
            stem="类似地，你可以利用信息技术绘制幂函数图象，认识变化规律。",
            options={},
            kind="free_response",
            readings=[{"content_kind": "prose"}, {"content_kind": "prose"}],
        ))

    def test_worked_explanation_is_not_promoted_by_a_quoted_comma_then_ask(self):
        explanation = (
            "在问题 1 中，求经过多少年游客人次是原来的 2 倍，就是计算 "
            "$x=\\log_{1.11}2$ 的值。由换底公式可得结果。"
        )
        self.assertFalse(pipeline._source_kind_has_question_support(
            expected_kind="exercise",
            stem=explanation,
            options={},
            kind="free_response",
            readings=[
                {"content_kind": "example"},
                {"content_kind": "prose"},
                {"content_kind": "prose"},
            ],
        ))

    def test_number_warning_accepts_either_independent_reader(self):
        self.assertIsNone(pipeline._number_seen_flag(
            1, [{"number_seen": 2}, {"number_seen": 1}],
        ))
        self.assertEqual(
            pipeline._number_seen_flag(1, [{"number_seen": 2}, None]),
            "AI 看到的题号是 2，请确认",
        )
        self.assertEqual(
            pipeline._number_seen_flag(1, [None, {"number_seen": 2}]),
            "AI 看到的题号是 2，请确认",
        )

    def test_second_read_and_arbiter_are_not_requested(self):
        primary_engine = readers.Engine("minimax", "primary-model")
        checker_engine = readers.Engine("siliconflow", "checker-model")
        primary = readers.parse_reading(
            "【题号】1\n【题型】解答题\n【题干】求 $x$ 的值。",
            1,
        )
        checker = readers.parse_reading(
            "【题号】1\n【题型】解答题\n【题干】求 $y$ 的值。",
            1,
        )
        snapshot = {
            "id": 1, "number": 1, "group_id": None,
            "regions": [{"page_idx": 0, "bbox": [0, 0, 10, 10]}],
            "candidates": [], "question_type": "free_response",
            "source_kind": Question.SourceKind.UNKNOWN,
            "segmentation_flags": [],
        }
        fake_store = type("Store", (), {"load": lambda self, page: object()})()

        with patch.object(pipeline.readers, "primary_engine", return_value=primary_engine), \
                patch.object(pipeline.readers, "checker_engine", return_value=checker_engine) as checker_mock, \
                patch.object(pipeline.readers, "arbiter_engine", return_value=primary_engine), \
                patch.object(
                    pipeline.readers, "read_question",
                    side_effect=lambda _engine, _url, _number, with_figures: (
                        primary if with_figures else checker
                    ),
                ), \
                patch.object(pipeline.readers, "chat", return_value="没有要求的结构化标签"), \
                patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])), \
                patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:image/jpeg;base64,x"):
            result = pipeline.read_card(snapshot, fake_store)

        checker_mock.assert_not_called()
        self.assertEqual(result["state"], Question.State.GREEN)
        self.assertEqual(result["stem"], primary["stem"])
        self.assertEqual(result["text_source"], "single")
        self.assertEqual(result["read_b"], {"skipped": "disabled"})
        self.assertEqual(result["read_c"], {})


class LocalTextReviewUpgradeTests(TestCase):
    def setUp(self):
        from .models import Paper

        self.paper = Paper.objects.create(
            filename="local-review.pdf", kind="pdf", sha256="d" * 64,
            source_path="data/local-review/source.pdf",
        )

    def test_batch_upgrade_keeps_raw_reads_and_unrelated_warnings(self):
        read_a = {
            "content_kind": "exercise", "number_seen": 5,
            "stem": "原始证据 A", "raw": "raw A",
        }
        read_b = {
            "content_kind": "prose", "number_seen": 5,
            "stem": "原始证据 B", "raw": "raw B",
        }
        read_c = {"content_kind": "exercise", "stem": "原始证据 C", "raw": "raw C"}
        question = Question.objects.create(
            paper=self.paper, number=5, source_kind=Question.SourceKind.EXERCISE,
            stem="已知直角三角形面积为 50，最小值是多少？",
            question_type="free_response", read_a=read_a, read_b=read_b, read_c=read_c,
            flags=[
                "本地版面规则判为练习题，但 AI 判为教材正文，请对照原书确认",
                "两次识读不一致，已由第三次识读裁决",
            ],
            state=Question.State.YELLOW,
        )

        stats = pipeline.persist_local_text_review_upgrades([question])

        question.refresh_from_db()
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(question.flags, ["两次识读不一致，已由第三次识读裁决"])
        self.assertEqual(question.state, Question.State.YELLOW)
        self.assertEqual(question.read_a, read_a)
        self.assertEqual(question.read_b, read_b)
        self.assertEqual(question.read_c, read_c)

    def test_batch_upgrade_removes_stale_number_warning_when_checker_is_correct(self):
        question = Question.objects.create(
            paper=self.paper, number=1, source_kind=Question.SourceKind.EXERCISE,
            stem="(1) 选择正确答案。", question_type="single_choice",
            read_a={"content_kind": "exercise", "number_seen": 2},
            read_b={"content_kind": "exercise", "number_seen": 1},
            flags=["AI 看到的题号是 2，请确认"], state=Question.State.YELLOW,
        )

        pipeline.persist_local_text_review_upgrades([question])

        question.refresh_from_db()
        self.assertEqual(question.flags, [])
        self.assertEqual(question.state, Question.State.GREEN)

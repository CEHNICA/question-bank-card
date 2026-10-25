"""卷面标题写着填空题、卡上却是解答题——三张这样的卡都是绿的，没人提示。

`qtypes.with_section` 只在选择题内部按标题定单选/多选，非选择题之间按设计不动：
标题是硬证据，但把一张判对的卡改错同样麻烦。这里只提示、不擅自改。

真卷量过（11 份卷子 133 张卡，标题写着填空题或解答题）：只有 3 张对不上——
胜利初四月考第 13 题（题干末尾「等于____米」，是填空却判成解答）、第 16 题
（「计算：…」判成填空）、胜利初一第 17 题（「把下列各数填入它所属的集合内，
(1)(2)(3)」判成填空）。

同一笔里把【分析】补进解析标记：答案版上有一页开头就是【分析】，之前裁到那里
为止，现在能继续往下裁（答案版第 24 题，全库只有这一张因此改变）。

    .\\backend\\.venv\\Scripts\\python.exe backend\\manage.py test core.test_section_kind
"""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from core import figure_policy, pipeline, readers
from core.models import Question

FILL_SECTION = "二、填空题（本大题共5个小题，每小题4分，共20分。请直接填写答案。）"
SOLVE_SECTION = "三、解答题（本大题共10个小题，共90分，解答应写出文字说明，证明过程或演算步骤。）"


class SectionKindTests(SimpleTestCase):
    def check(self, section, kind):
        return pipeline._section_kind_conflict(section, kind)

    def test_a_fill_blank_section_read_as_free_response(self):
        self.assertEqual(self.check(FILL_SECTION, "free_response"), ("填空题", "解答题"))

    def test_a_free_response_section_read_as_fill_blank(self):
        self.assertEqual(self.check(SOLVE_SECTION, "fill_blank"), ("解答题", "填空题"))

    def test_matching_types_are_quiet(self):
        self.assertIsNone(self.check(FILL_SECTION, "fill_blank"))
        self.assertIsNone(self.check(SOLVE_SECTION, "free_response"))

    def test_a_choice_section_is_left_to_the_choice_rules(self):
        self.assertIsNone(self.check("一、选择题（本大题共10个小题）", "fill_blank"))
        self.assertIsNone(self.check("一、选择题（本大题共10个小题）", "single_choice"))

    def test_the_heading_prefix_is_what_counts(self):
        # “三、解答题（含填空）” still says 解答题; the word 填空 in parentheses
        # must not turn every card in the section into a conflict.
        self.assertEqual(self.check("三、解答题（含填空）", "fill_blank"),
                         ("解答题", "填空题"))
        self.assertIsNone(self.check("三、解答题（含填空）", "free_response"))

    def test_an_unknown_or_missing_side_is_quiet(self):
        self.assertIsNone(self.check(SOLVE_SECTION, ""))
        self.assertIsNone(self.check(SOLVE_SECTION, "unknown"))
        self.assertIsNone(self.check("", "fill_blank"))
        self.assertIsNone(self.check(None, "fill_blank"))
        self.assertIsNone(self.check("三、解答题", "single_choice"))

    def test_a_bare_keyword_without_the_section_number_is_quiet(self):
        # The stored heading is the whole heading line.  Without the “N、” prefix a
        # bare “填空题” may just be a continuation line, so it must not be judged —
        # loosening the prefix to optional is exactly the failure this guards.
        self.assertIsNone(self.check("解答应写出文字说明", "fill_blank"))
        self.assertIsNone(self.check("填空题（本大题共5个小题）", "free_response"))


class AnalysisMarkTests(SimpleTestCase):
    """【分析】是解析的一部分，裁图和数「印在解析之后的图」都要算进去。"""

    def test_the_marker_covers_analysis(self):
        self.assertTrue(pipeline._SOLUTION_MARK.search("【分析】本题考查勾股定理"))

    def test_a_page_that_starts_with_analysis_is_cut_there(self):
        snapshot = {
            "regions": [{"page_idx": 1, "bbox": [60.0, 40.0, 930.0, 900.0]}],
            "witness_blocks": [{"page_idx": 1, "bbox": [87.0, 300.0, 912.0, 340.0],
                                "text": "【分析】本题考查勾股定理"}],
        }
        self.assertEqual(pipeline._reading_regions(snapshot),
                         [{"page_idx": 1, "bbox": [60.0, 40.0, 930.0, 298.0]}])

    def test_an_analysis_marked_figure_counts_as_from_the_solution(self):
        blocks = [{"page_idx": 1, "bbox": [87.0, 300.0, 912.0, 340.0],
                   "text": "【分析】本题考查勾股定理"}]
        candidates = [{"label": "1", "page_idx": 1, "bbox": [87.0, 500.0, 300.0, 620.0]}]
        self.assertEqual(
            pipeline._unclassified_below_solution(blocks, candidates, {}), (1, 1))


READING = {"stem": "已知在阳光下，垂直于地面高1米的标杆的影长也为1米，如图．则树高 $AB$ 等于____米．",
           "options": {}, "type": "free_response", "content_kind": "exercise", "figures": {},
           "missing_figure": False, "number_seen": 13, "figure_descriptions": [],
           "unclear": False, "unreadable": False}


class SectionKindWiringTests(SimpleTestCase):
    def read(self, reading, section):
        snapshot = {
            "id": 1, "number": 13, "group_id": None,
            "regions": [{"page_idx": 1, "bbox": [60.0, 300.0, 930.0, 700.0]}],
            "candidates": [], "question_type": "free_response", "section": section,
            "stem": "", "options": {}, "edited": False,
            "source_kind": Question.SourceKind.UNKNOWN, "source_anchor_seq": 8,
            "segmentation_flags": [], "body_mode": "text", "start_source": "mineru",
            "witness_blocks": [{"page_idx": 1, "bbox": [80.0, 320.0, 700.0, 360.0],
                                "text": "13. 已知在阳光下，垂直于地面高1米的标杆的影长也为1米"}],
        }
        with mock.patch.object(readers, "primary_engine", return_value=object()), \
                mock.patch.object(readers, "read_question", return_value=reading), \
                mock.patch.object(pipeline.imaging, "stack_regions",
                                  return_value=(SimpleNamespace(height=700, width=1400), [])), \
                mock.patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:url"):
            return pipeline.read_card(snapshot, SimpleNamespace(load=lambda page: object()))

    def test_the_conflict_lands_on_the_card_and_yellows_it(self):
        result = self.read(READING, FILL_SECTION)
        self.assertTrue(any("卷面的大题标题写着这一段是填空题" in f for f in result["flags"]))
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_a_matching_type_leaves_the_card_quiet(self):
        result = self.read({**READING, "type": "fill_blank"}, FILL_SECTION)
        self.assertFalse(any("卷面的大题标题写着这一段是填空题" in f for f in result["flags"]))
        self.assertNotIn(figure_policy.FLAG_UNFOUND_FIGURE, result["flags"])

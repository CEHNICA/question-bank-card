"""1.10.1：照片卷的切线、草稿续页、多选大题、编出来的“此处为配图”。

起因是一份两栏横版照片卷（高一质量检测一，满是手写演算）：切线压在
字上，第 6 题选项的分母跑到第 7 题顶上；第 7、16 题拼上了右栏顶上的草稿；
“有多项符合题目要求”的第 9、10 题记成了单选。真实试卷不进仓库，这里
用画出来的页面和虚构的 MinerU 框重现同样的几何。
"""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase, TestCase
from PIL import Image, ImageDraw

from . import cuts, pipeline, qtypes, readers, segment
from . import test_v110_types_origin as v110
from .models import Block, Question, QuestionGroup
from .tests import PAGES, block, tagged

COLUMN = [16.0, 492.0]


def page(lines=(), strokes=(), height_px=2000):
    """A white page 1000 units wide/high drawn at 2 px per unit (height).

    lines: (y0, y1) bands of solid print across the left column;
    strokes: (x0, y0, x1, y1) small dark marks (denominators, underlines, working).
    """
    width_px = 1500
    image = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(image)
    sx, sy = width_px / 1000, height_px / 1000
    for y0, y1 in lines:
        draw.rectangle((60 * sx, y0 * sy, 470 * sx, y1 * sy), fill="black")
    for x0, y0, x1, y1 in strokes:
        draw.rectangle((x0 * sx, y0 * sy, x1 * sx, y1 * sy), fill="black")
    return image


def item(number, regions, y, source="mineru", at_start=True, x=60.0):
    """A segmented question; its number is printed at the left end of the drawn lines (x=60)."""
    return {"number": number,
            "start": {"page": 0, "col": 0, "y": y, "x": x, "source": source, "at_start": at_start},
            "regions": [{"page_idx": 0, "bbox": list(bbox)} for bbox in regions]}


class InkCutTests(SimpleTestCase):
    def test_a_loose_box_no_longer_cuts_the_options_of_the_question_above(self):
        # 第 6 题选项行到 830，分母一直落到 856；第 7 题的字从 866 起，
        # MinerU 的框却从 854 起：原来第 6 题在 852 处被切，分母进了第 7 题。
        image = page(lines=[(800, 830), (866, 890)],
                     strokes=[(100, 836, 108, 856), (220, 836, 228, 856), (330, 836, 338, 856)])
        q6 = item(6, [[*COLUMN[:1], 765, COLUMN[1], 852]], y=774)
        q7 = item(7, [[COLUMN[0], 845, COLUMN[1], 972]], y=854)
        self.assertEqual(cuts.snap_cuts([q6, q7], lambda _p: image), 1)
        bottom, top = q6["regions"][0]["bbox"][3], q7["regions"][0]["bbox"][1]
        self.assertGreater(bottom, 856)   # 分母留在第 6 题
        self.assertGreater(top, 856)      # 也不再出现在第 7 题顶上
        self.assertLess(top, 866)         # 第 7 题第一行完整
        self.assertLessEqual(top, bottom)

    def test_a_clean_cut_on_a_printed_page_does_not_move(self):
        image = page(lines=[(800, 840), (870, 890)])
        q1 = item(1, [[COLUMN[0], 700, COLUMN[1], 866]], y=868)
        q2 = item(2, [[COLUMN[0], 859, COLUMN[1], 990]], y=868)
        self.assertEqual(cuts.snap_cuts([q1, q2], lambda _p: image), 0)
        self.assertEqual(q1["regions"][0]["bbox"][3], 866)
        self.assertEqual(q2["regions"][0]["bbox"][1], 859)

    def test_the_next_question_keeps_its_first_line(self):
        # 上一题最后一行压到 849；空白只有 849–853；下一题第一行 853–862，
        # 下面还有一条更宽的行距 862–872。切线必须落在上面那条窄缝里。
        image = page(lines=[(830, 849), (853, 862), (872, 890)])
        q1 = item(1, [[COLUMN[0], 700, COLUMN[1], 851]], y=760)
        q2 = item(2, [[COLUMN[0], 844, COLUMN[1], 990]], y=853)
        cuts.snap_cuts([q1, q2], lambda _p: image)
        self.assertLessEqual(q2["regions"][0]["bbox"][1], 853)
        self.assertGreaterEqual(q1["regions"][0]["bbox"][3], 849)

    def test_a_thin_numerator_over_the_next_number_stays_with_the_next_question(self):
        # 胜利初一第 2 题“2. −1/2024”：分子 1 印在题号行上方、MinerU 框的上沿处，
        # 细得在整行投影里像空白。上一题最后一行 480–497。
        image = page(lines=[(480, 497), (507, 519)], strokes=[(300, 500, 303, 506)])
        q1 = item(1, [[COLUMN[0], 300, COLUMN[1], 498]], y=310)
        q2 = item(2, [[COLUMN[0], 491, COLUMN[1], 700]], y=500)
        cuts.snap_cuts([q1, q2], lambda _p: image)
        self.assertLessEqual(q2["regions"][0]["bbox"][1], 500)

    def test_a_short_first_line_under_working_is_not_given_away(self):
        # 上一题的选项行 489–496（标签正在题号那一条上），手写演算 496–500 连到
        # 第 7 题只有 5.5 高的第一行 500–505.5。
        for working in ((100, 496, 470, 500), (16, 496, 470, 500)):   # 也盖住题号那一条
            image = page(lines=[(489, 496), (500, 505.5), (515, 530)], strokes=[working])
            q6 = item(6, [[COLUMN[0], 300, COLUMN[1], 498]], y=310)
            q7 = item(7, [[COLUMN[0], 491, COLUMN[1], 700]], y=500)
            cuts.snap_cuts([q6, q7], lambda _p: image)
            self.assertLessEqual(q7["regions"][0]["bbox"][1], 500, working)
            self.assertLessEqual(q6["regions"][0]["bbox"][3], 500, working)

    def test_a_question_ending_at_a_heading_keeps_its_underline(self):
        # 第 14 题最后一行的答题横线在 452.5–453.3，“四、解答题”的框从 454 起。
        image = page(lines=[(430, 450), (459, 470)], strokes=[(300, 452.5, 420, 453.0)])
        q14 = item(14, [[COLUMN[0], 375, COLUMN[1], 452]], y=384)
        for heading in ({"page": 0, "col": 0, "y": 454.0, "bottom": 470.0, "text": "四、解答题"},
                        {"page": 0, "col": 0, "x": 60.0, "y": 454.0, "bottom": 470.0, "text": "四、解答题"}):
            q14 = item(14, [[COLUMN[0], 375, COLUMN[1], 452]], y=384)
            self.assertEqual(cuts.snap_cuts([q14], lambda _p: image, mock.Mock(headings=[heading])), 1)
            bottom = q14["regions"][0]["bbox"][3]
            self.assertGreater(bottom, 453.3)
            self.assertLess(bottom, 459)

    def test_a_heading_whose_box_starts_below_its_ink_is_not_taken_in(self):
        image = page(lines=[(430, 449), (451, 462)])
        q14 = item(14, [[COLUMN[0], 375, COLUMN[1], 452]], y=384)
        heading = {"page": 0, "col": 0, "x": 60.0, "y": 454.0, "bottom": 470.0, "text": "四、解答题"}
        cuts.snap_cuts([q14], lambda _p: image, mock.Mock(headings=[heading]))
        self.assertEqual(q14["regions"][0]["bbox"][3], 452)

    def test_a_heading_in_the_other_column_is_not_this_questions_stop(self):
        image = page(lines=[(430, 450)], strokes=[(300, 452.5, 420, 453.5)])
        q14 = item(14, [[COLUMN[0], 375, COLUMN[1], 452]], y=384)
        heading = {"page": 0, "col": 1, "x": 540.0, "y": 454.0, "bottom": 470.0, "text": "四、解答题"}
        self.assertEqual(cuts.snap_cuts([q14], lambda _p: image, mock.Mock(headings=[heading])), 0)

    def test_working_above_the_first_question_of_a_column_is_left_out(self):
        # 右栏顶上 43–69 是手写演算，第 17 题从 72 起；原来的上沿 63 压在演算上。
        image = page(lines=[(43, 69), (72, 90)])
        q17 = item(17, [[COLUMN[0], 63, COLUMN[1], 425]], y=72)
        self.assertEqual(cuts.snap_cuts([q17], lambda _p: image), 1)
        top = q17["regions"][0]["bbox"][1]
        self.assertGreaterEqual(top, 69)
        self.assertLessEqual(top, 72)

    def test_estimated_and_located_starts_are_not_snapped(self):
        image = page(lines=[(800, 830), (866, 890)], strokes=[(100, 836, 108, 856)])
        for start in ({"source": "located"}, {"at_start": False}):
            q6 = item(6, [[COLUMN[0], 765, COLUMN[1], 852]], y=774)
            q7 = item(7, [[COLUMN[0], 845, COLUMN[1], 972]], y=854)
            q7["start"].update(start)
            self.assertEqual(cuts.snap_cuts([q6, q7], lambda _p: image), 0, start)

    def test_a_page_that_cannot_be_read_leaves_the_cuts_alone(self):
        q6 = item(6, [[COLUMN[0], 765, COLUMN[1], 852]], y=774)
        q7 = item(7, [[COLUMN[0], 845, COLUMN[1], 972]], y=854)

        def broken(_page):
            raise OSError("missing page")

        self.assertEqual(cuts.snap_cuts([q6, q7], broken), 0)
        self.assertEqual(q6["regions"][0]["bbox"][3], 852)


def scratch_paper(top_right_text, kind="text", options=True):
    """Left column ends with question 7; the right column starts with something, then 8."""
    seven = ("7. 已知 $\\alpha:x>m$，则实数 $m$ 的取值范围是（ ）A. $m\\geq 2$ B. $m>2$ C. $m<2$ D. $m\\leq 2$"
             if options else "7. （15分）已知命题 $p$：$\\forall x\\geq 1$，求实数 $a$ 的取值范围.")
    return [
        block(0, 0, [60, 100, 470, 130], "一、选择题：在每小题给出的四个选项中，只有一个选项是正确的."),
        block(1, 0, [60, 700, 470, 760], "6. 下列函数中，在区间 $(-\\infty,0)$ 上单调递减的是（ ）"),
        block(2, 0, [60, 850, 470, 920], seven),
        block(3, 0, [540, 14, 950, 80], top_right_text, kind),
        block(4, 0, [540, 90, 950, 230], "8. 若存在 $x\\geq 0$，使不等式成立，则实数 $m$ 的取值范围是（ ）"),
    ]


class ScratchSpillTests(SimpleTestCase):
    def regions_of(self, blocks, number):
        result = segment.segment(PAGES[:1], blocks)
        return next(q for q in result["questions"] if q["number"] == number)["regions"]

    def question(self, blocks, number):
        result = segment.segment(PAGES[:1], blocks)
        return next(q for q in result["questions"] if q["number"] == number)

    def test_formula_only_next_column_is_kept_as_a_review_candidate_not_merged(self):
        working = "$\\frac{3(y+1)}{(x+1)(y+1)}$ $\\frac{4(x+1)}{(x+1)(y+1)}$ + $\\frac{4}{y+3xy}$"
        for text, kind in ((working, "text"), ("$$(x-10)(-2x+60)$$", "equation")):
            with self.subTest(kind=kind):
                seven = self.question(scratch_paper(text, kind), 7)
                self.assertEqual(len(seven["regions"]), 1)
                self.assertEqual(len(seven["segmentation_notes"]), 1)
                self.assertEqual(seven["segmentation_flags"], seven["segmentation_notes"])
                self.assertFalse(seven["segmentation"]["range_limited"])
                self.assertIn("暂未并入题卡范围", seven["segmentation_flags"][0])
                diagnostic = seven["segmentation_diagnostics"][0]
                self.assertEqual(diagnostic["code"], "ambiguous_formula_continuation")
                self.assertEqual(diagnostic["action"], "pending_review")
                self.assertEqual(diagnostic["page_idx"], 0)
                self.assertNotIn({"page_idx": diagnostic["page_idx"], "bbox": diagnostic["bbox"]}, seven["regions"])

    def test_a_display_formula_after_an_open_question_is_deferred_for_review(self):
        # A recurrence can be printed content or working; OCR text cannot decide.
        seven = self.question(scratch_paper("$$a_{n+1}=2a_n+1$$", "equation", options=False), 7)
        self.assertEqual(len(seven["regions"]), 1)
        self.assertEqual(len(seven["segmentation_notes"]), 1)
        self.assertIn("第 7 题", seven["segmentation_notes"][0])
        self.assertTrue(seven["segmentation_flags"])
        self.assertEqual(seven["segmentation_diagnostics"][0]["action"], "pending_review")
        self.assertFalse(seven["segmentation"]["range_limited"])

    def test_a_formula_only_next_page_keeps_its_matrix_and_source_coordinates(self):
        blocks = [
            block(0, 0, [60, 800, 950, 900], "1. 已知矩阵如下，求矩阵的行列式。"),
            block(1, 1, [60, 20, 950, 90], "$$\\begin{pmatrix}a&b\\\\c&d\\end{pmatrix}$$", "equation"),
            block(2, 1, [60, 110, 950, 180], "2. 计算下列各式的值。"),
        ]
        result = segment.segment(PAGES[:2], blocks)
        one = next(question for question in result["questions"] if question["number"] == 1)
        self.assertEqual([region["page_idx"] for region in one["regions"]], [0])
        self.assertEqual(one["segmentation_diagnostics"][0]["page_idx"], 1)
        self.assertNotIn({"page_idx": one["segmentation_diagnostics"][0]["page_idx"],
                          "bbox": one["segmentation_diagnostics"][0]["bbox"]}, one["regions"])
        self.assertEqual(one["segmentation_diagnostics"][0]["action"], "pending_review")
        self.assertTrue(one["segmentation_flags"])
        self.assertFalse(one["segmentation"]["range_limited"])

    def test_the_numbered_opening_is_not_an_ambiguous_continuation(self):
        result = segment.segment(PAGES[:1], [
            block(0, 0, [60, 100, 950, 180], "1. $$a_{n+1}=2a_n+1$$", "equation"),
            block(1, 0, [60, 220, 950, 300], "2. 计算下列各式的值。"),
        ])
        one = next(question for question in result["questions"] if question["number"] == 1)
        self.assertEqual(one["segmentation_diagnostics"], [])
        self.assertEqual(one["segmentation_flags"], [])

    def test_book_builder_keeps_formula_review_flag_and_the_complete_continuation(self):
        blocks = [
            block(0, 0, [60, 800, 950, 900], "1. 已知数列满足如下关系，求数列通项。"),
            block(1, 1, [60, 20, 950, 90], "$$a_{n+1}=2a_n+1$$", "equation"),
            block(2, 1, [60, 110, 950, 180], "2. 计算下列各式的值。"),
        ]
        layout, starts = segment.analyse(PAGES[:2], blocks)
        for start in starts:
            start.source_kind = "exercise"
        one = segment.build_book_questions(layout, starts, blocks)[0]
        self.assertEqual([region["page_idx"] for region in one["regions"]], [0, 1])
        self.assertTrue(one["segmentation_flags"])
        self.assertFalse(one["segmentation"]["range_limited"])
        self.assertTrue(segment.center_in_regions(1, blocks[1]["bbox"], one["regions"]))

    def test_book_safety_limit_is_explicit_when_it_stops_an_ambiguous_continuation(self):
        pages = [{**PAGES[0], "page_idx": index} for index in range(6)]
        blocks = [block(0, 0, [60, 800, 950, 900], "1. 已知数列满足下列关系，求数列通项。")]
        blocks.extend(block(index, index, [60, 20, 950, 90], "$$a_{n+1}=2a_n+1$$", "equation")
                      for index in range(1, 6))
        blocks.append(block(6, 5, [60, 110, 950, 180], "2. 计算下列各式的值。"))
        layout, starts = segment.analyse(pages, blocks)
        for start in starts:
            start.source_kind = "exercise"
        one = segment.build_book_questions(layout, starts, blocks)[0]
        self.assertEqual(len({region["page_idx"] for region in one["regions"]}), segment.BOOK_MAX_CARD_PAGES)
        self.assertTrue(one["segmentation"]["range_limited"])
        self.assertTrue(any("安全上限" in flag for flag in one["segmentation_flags"]))
        self.assertEqual([diagnostic["action"] for diagnostic in one["segmentation_diagnostics"]],
                         ["retained", "retained", "retained", "range_limited", "range_limited"])

    def test_printed_rest_of_the_question_still_follows_it(self):
        for text, kind in (("B. $\\forall x>0,y>0$", "text"),
                           ("(2) 若 $b=c$，解关于 $x$ 的不等式", "text"),
                           ("则实数 $a$ 的取值范围是", "text"),
                           ("$\\mathrm{A}.\\ \\frac12\\quad\\mathrm{B}.\\ 1$", "text"),
                           ("$A.\\ 1$", "text"),
                           ("Which of the following statements is true", "text"),
                           ("", "image")):
            seven = self.question(scratch_paper(text, kind), 7)
            regions = seven["regions"]
            self.assertEqual(len(regions), 2, text or kind)
            self.assertGreaterEqual(regions[1]["bbox"][0], 490)
            self.assertEqual(seven["segmentation_diagnostics"], [])
            self.assertEqual(seven["segmentation_flags"], [])


class SectionTypeTests(SimpleTestCase):
    MULTI = ("二、选择题：本题共3小题，每小题6分，共18分.在每小题给出的选项中，有多项符合题目要求."
             "全部选对的得6分，部分选对的得部分分，有选错的得0分.")
    SINGLE = "一、选择题：本大题共8小题，每小题5分，共计40分.每小题给出的四个选项中，只有一个选项是正确的."
    MIXED = "一、选择题：第1~8题只有一项符合题目要求，第9~11题有多项符合题目要求."

    def test_the_instruction_under_a_choice_heading_decides_single_or_multiple(self):
        self.assertEqual(qtypes.section_kind(self.MULTI), "multiple_choice")
        self.assertEqual(qtypes.section_kind(self.SINGLE), "single_choice")
        self.assertEqual(qtypes.section_kind(self.MIXED), "unknown")
        self.assertEqual(qtypes.section_kind("三、填空题：本大题共3小题"), "unknown")
        self.assertEqual(segment._section_type(self.MULTI), "multiple_choice")
        self.assertEqual(segment._section_type(self.MIXED), "single_choice")   # 仍按“选择题”
        self.assertEqual(segment._section_type("三、解答题"), "free_response")

    def test_wording_that_only_looks_like_a_rule_fixes_nothing(self):
        # A single-choice rule that forbids picking several.
        self.assertEqual(qtypes.section_kind(
            "一、选择题：在每小题给出的四个选项中，选出符合题目要求的一项，多选、错选、不选均不得分."), "unknown")
        # A mixed heading cut short after its first half (headings keep 80 characters).
        self.assertEqual(qtypes.section_kind("一、选择题：第 $1 \\sim 8$ 题只有一项符合题目要求"), "unknown")
        self.assertEqual(qtypes.section_kind("一、选择题：1~8题只有一项符合题目要求"), "unknown")
        self.assertEqual(qtypes.with_section("multiple_choice", "一、选择题：第1~8题只有一项符合题目要求"),
                         "multiple_choice")

    def test_only_choice_readings_follow_the_heading(self):
        self.assertEqual(qtypes.with_section("single_choice", self.MULTI), "multiple_choice")
        self.assertEqual(qtypes.with_section("multiple_choice", self.SINGLE), "single_choice")
        self.assertEqual(qtypes.with_section("fill_blank", self.MULTI), "fill_blank")
        self.assertEqual(qtypes.with_section("single_choice", self.MIXED), "single_choice")
        self.assertEqual(qtypes.with_section("single_choice", ""), "single_choice")

    def test_segmented_cards_under_a_multiple_heading_are_multiple_choice(self):
        blocks = [
            block(0, 0, [60, 100, 900, 140], self.MULTI),
            block(1, 0, [60, 150, 900, 200], "9. 下列各函数中，最小值为2的是（ ）"),
            block(2, 0, [60, 300, 900, 350], "10. 已知关于实数 x,y 的二元函数，则（ ）"),
        ]
        result = segment.segment(PAGES[:1], blocks)
        self.assertEqual([q["question_type"] for q in result["questions"]], ["multiple_choice"] * 2)


class SectionReadCardTests(v110.TempDataMixin, TestCase):
    """The reader says 单选 for every choice question; the section says 多项."""

    setUp = v110.ReadCardTypeTests.setUp
    read = v110.ReadCardTypeTests.read

    def test_a_single_choice_reading_under_a_multiple_heading_is_multiple_choice(self):
        self.snapshot = {**self.snapshot, "section": SectionTypeTests.MULTI}
        options = {"A": "$y=x^2-6x+10$", "B": "$y=x-2\\sqrt{x}+3$", "C": "$y=x+\\dfrac{1}{x}$", "D": "$y=2$"}
        result = self.read(tagged("下列各函数中，最小值为 2 的是（ ）", options),
                           tagged("下列各函数中，最小值为 2 的是（ ）", options))
        self.assertEqual(result["question_type"], "multiple_choice")

    def test_snapshot_carries_the_section(self):
        question = Question.objects.create(paper=self.paper, number=9, section=SectionTypeTests.MULTI,
                                           regions=[{"page_idx": 0, "bbox": [50, 100, 480, 300]}])
        self.assertEqual(pipeline._snapshot(question)["section"], SectionTypeTests.MULTI)


class InventedFigureNoteTests(SimpleTestCase):
    def test_a_note_instead_of_an_option_counts_as_not_read(self):
        raw = ("【题型】多选题\n【题干】\n已知关于实数 $x,y$ 的二元函数 $f(x,y)=(x+1)y$，则\n"
               "【A】（原卷此处为配图，无印刷文字）\n【B】$\\forall x>0,y>0$\n【C】$f(2x,x-a)\\geq -a-2$\n【D】$a\\geq 3$")
        reading = readers.parse_reading(raw, 11)
        self.assertNotIn("A", reading["options"])
        self.assertIn("A", reading["figure_descriptions"])

    def test_printed_brackets_are_not_notes(self):
        for text in ("（2022年北京冬奥会期间）某商店", "如图（1）所示", "（图形）", "$f(1,5)=f(5,1)$"):
            self.assertEqual(readers.strip_bracketed_figure_descriptions(text), (text, False))
        for text in ("（此处为图片）", "（无印刷文字）", "[图：数轴]"):
            self.assertEqual(readers.strip_bracketed_figure_descriptions(text)[0], "", text)


class PipelineUsesInkCutsTests(v110.TempDataMixin, TestCase):
    def test_segmentation_snaps_cuts_with_the_page_store_and_layout(self):
        self.use_temp_data()
        paper = self.make_paper()
        group = QuestionGroup.objects.create(paper=paper, title="试卷", sequence=0)
        for item_block in scratch_paper("B. $\\forall x>0$"):
            Block.objects.create(paper=paper, seq=item_block["seq"], type=item_block["type"], page_idx=0,
                                 bbox=item_block["bbox"], text=item_block["text"])
        calls = []

        def spy(items, loader, layout):
            calls.append(([item["number"] for item in items], callable(loader), layout is not None))
            return 0

        with mock.patch.object(cuts, "snap_cuts", side_effect=spy):
            pipeline._collect_segmentation_items(paper, [group], locate_gaps=False,
                                                 page_store=pipeline.ReadOnlyPageStore(paper))
        self.assertEqual(calls, [([6, 7, 8], True, True)])


class SectionTidyTests(v110.TempDataMixin, TestCase):
    """Cards already read by 1.10 under a 多项 heading are corrected on start."""

    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()

    def choice(self, number, **extra):
        values = {"question_type": "single_choice", "section": SectionTypeTests.MULTI,
                  "stem": "下列各函数中，最小值为 2 的是（ ）",
                  "options": {"A": "$1$", "B": "$2$", "C": "$3$", "D": "$4$"},
                  "state": Question.State.GREEN, "flags": []}
        values.update(extra)
        return self.card(self.paper, number=number, **values)

    def test_unreviewed_cards_become_multiple_choice_and_reviewed_ones_stay(self):
        from . import library
        fresh = self.choice(9)
        locked = self.choice(10, type_locked=True)
        edited = self.choice(11, edited=True)
        library.tidy_saved_cards()
        for question, kind in ((fresh, "multiple_choice"), (locked, "single_choice"), (edited, "single_choice")):
            question.refresh_from_db()
            self.assertEqual(question.question_type, kind, question.number)
        # Running again changes nothing.
        self.assertEqual(library.tidy_saved_cards()["questions"], 0)


class SnapToleranceTests(v110.TempDataMixin, TestCase):
    """Re-cutting a paper read before 1.10.1 leaves approved crops alone."""

    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.blocks = [block(1, 0, [60, 120, 470, 160], "6. 下列函数中，单调递减的是（ ）"),
                       block(2, 0, [60, 300, 470, 340], "7. 已知 $a>b$，则（ ）")]

    def test_small_edge_moves_are_snap_only(self):
        old = [{"page_idx": 0, "bbox": [16, 100, 492, 290]}]
        self.assertTrue(pipeline._snap_only_change(old, [{"page_idx": 0, "bbox": [16, 100, 492, 298.5]}]))
        self.assertFalse(pipeline._snap_only_change(old, [{"page_idx": 0, "bbox": [16, 100, 492, 320]}]))
        self.assertFalse(pipeline._snap_only_change(old, [{"page_idx": 1, "bbox": [16, 100, 492, 290]}]))
        self.assertFalse(pipeline._snap_only_change(old, old + old))

    def test_only_protected_cards_keep_their_old_crop(self):
        old = [{"page_idx": 0, "bbox": [16, 100, 492, 290]}]
        new = [{"page_idx": 0, "bbox": [16, 100, 492, 296]}]
        approved = self.card(self.paper, number=6, regions=old, regions_auto=old, approved=True)
        fresh = self.card(self.paper, number=8, regions=old, regions_auto=old)
        self.assertTrue(pipeline._keeps_protected_range(approved, new, self.blocks))
        self.assertFalse(pipeline._keeps_protected_range(fresh, new, self.blocks))
        # A move that changes which printed lines are inside is a real change.
        taller = [{"page_idx": 0, "bbox": [16, 100, 492, 299]}]
        moved_blocks = [*self.blocks, block(3, 0, [60, 292, 470, 297], "D. $2$")]
        self.assertFalse(pipeline._keeps_protected_range(approved, taller, moved_blocks))

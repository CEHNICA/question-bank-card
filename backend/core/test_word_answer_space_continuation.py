"""第七十一轮守护：导出 Word 的答题留白必须**照着预览的分段写**，并补上续页标题。

实测出来的缺陷（探针 _probe138.py / _probe139.py，40 题对照卷，真实 Word）：

    预览（Chrome 打印媒体实测）      Word（COM 自然排版实测）
    p1 题6 留白 24.6 毫米             题6 留白 20.1 毫米（p1）
    p2 题6 留白  5.4 毫米 + 标题      题6 留白  9.9 毫米（p2），**没有标题**
    p3 题13 留白 30 毫米 + 标题       题13 留白整块在 p3，**没有标题**
    ……共 6 处续页（题 6/13/19/26/32/39），Word 一处标题都没有

预览的留白是**可切**的（frontend/exam-layout.js 的 writingSpace）：页底放不下的
那几毫米挪到下一页，续页顶部打一行「第 N 题答题区（续）」。Word 原来一律把
30 毫米切成 20.1 + 9.9 —— 切点跟预览不是一回事，续页上那行提示更是压根没有：
学生在续页上看到一块答题区，不知道是哪道题的。

修法：前端把预览量好的留白分段（measured_answer_splits：哪一份在首页、哪一份
在续页）送过来，Word 照着写 —— 首页那份按 20.1 毫米的粒度切（保住「放不下就
整段走」的阈值），续页那份单独成段，前面补一行标题。

判据全是独立字面值（预览的 CSS 像素 + 毫米换算），不引用被测模块的常量。
"""

import io

from django.test import SimpleTestCase
from docx.oxml.ns import qn

from . import library_drafts

QUESTION_ID = "33333333-3333-4333-8333-333333333333"
OTHER_ID = "44444444-4444-4444-8444-444444444444"

# 预览 .exam-continuation-label（frontend/library.css:951）：
# font-size 10px、line-height 1.35（行盒 13.5px）、color #666、margin 0 0 4px。
# 预览正文 16px = 12 磅 → 1px = 0.75 磅。
PREVIEW_LABEL_SIZE_PX = 10
PREVIEW_LABEL_LINE_BOX_PX = 13.5
PREVIEW_LABEL_AFTER_PX = 4
PREVIEW_LABEL_COLOR = "666666"
# 预览的题块下外边距（.print-question 的 margin-bottom）。
QUESTION_GAP_TWIPS = 160
# 留白单段上限 = 预览的 MIN_WRITABLE（76px = 20.1 毫米）。
PREVIEW_MIN_WRITABLE_TWIPS = 1140
# 毫米换算。
THIRTY_MM_TWIPS = 1701
# 预览量出来的「续页那一份」：5.4 毫米 = 306 twips（首页那一份是 24.6 毫米）。
CONTINUATION_PIECE_TWIPS = 306


def build_item(question_id=QUESTION_ID, stem="题干示例。"):
    return {
        "id": question_id,
        "type": "free_response",
        "where": "第 1 题",
        "content": {"options": {}, "body_mode": "text"},
        "fields": {"stem": [{"type": "text", "segments": [{"type": "text", "raw": stem}]}]},
        "images": [],
    }


def build_document(items=None, answer_space="auto", splits=None, font_size=12):
    """走**真实导出路径**造一份 Word。"""
    from . import library_export

    options = {
        "font_size": font_size,
        "student_info": False,
        "answer_space": answer_space,
        "pagination": "none",
        "question_breaks": set(),
        "answer_layout": "appendix",
    }
    if splits is not None:
        options["measured_answer_splits"] = splits
    data = library_export._document(items or [build_item()], "第七十一轮", options, "questions")
    from docx import Document

    return Document(io.BytesIO(data))


def spacing_of(element):
    return element.find(qn("w:pPr") + "/" + qn("w:spacing"))


def twips(spacing, name):
    value = spacing.get(qn(f"w:{name}"))
    return int(value) if value is not None else None


def text_of(element):
    return "".join(node.text or "" for node in element.findall(".//" + qn("w:t")))


def answer_space_segments(document):
    """导出结果里所有「固定行高的空段」—— 留白块的一段。"""
    found = []
    for element in document._element.body:
        if element.tag != qn("w:p"):
            continue
        spacing = spacing_of(element)
        if spacing is None or spacing.get(qn("w:lineRule")) != "exact":
            continue
        if text_of(element).strip():
            continue
        found.append(element)
    return found


def continuation_labels(document):
    """导出结果里的「第 N 题答题区（续）」标题段。"""
    return [
        element
        for element in document._element.body
        if element.tag == qn("w:p") and "题答题区（续）" in text_of(element)
    ]


def keep_next_value(element):
    """这一段有没有「跟下一段同页」的约束。

    OOXML 里 ``<w:keepNext/>`` **不写 w:val 就是 true**（val 默认 true），
    只有 ``w:val="0"`` 才是关掉。所以判据不能只看 val 的字面值。
    """
    props = element.find(qn("w:pPr"))
    if props is None:
        return False
    flag = props.find(qn("w:keepNext"))
    if flag is None:
        return False
    value = flag.get(qn("w:val"))
    if value is None:
        return True
    return value not in ("0", "false", "off")


class MeasurementTests(SimpleTestCase):
    """尺子自检：预览的像素换算成磅/毫米。"""

    def test_the_preview_label_is_seven_and_a_half_points(self):
        """预览正文 16px = 12 磅 → 1px = 0.75 磅；标签 10px = 7.5 磅。"""
        self.assertAlmostEqual(PREVIEW_LABEL_SIZE_PX * 0.75, 7.5, places=3)

    def test_the_preview_label_line_box_is_thirteen_and_a_half_pixels(self):
        """line-height 1.35 × 10px = 13.5px = 10.125 磅。"""
        self.assertAlmostEqual(PREVIEW_LABEL_LINE_BOX_PX * 0.75, 10.125, places=3)

    def test_the_preview_label_takes_four_and_a_half_millimetres(self):
        """行盒 + 下外边距 = 预览续块里那行标题占的高度。"""
        millimetres = (PREVIEW_LABEL_LINE_BOX_PX + PREVIEW_LABEL_AFTER_PX) * 0.75 * 25.4 / 72
        self.assertAlmostEqual(millimetres, 4.63, delta=0.01)


class GroupNormalisationTests(SimpleTestCase):
    """分段计划要先归一化，对不上就当没量过。"""

    def setUp(self):
        from . import library_export

        self.groups = library_export._answer_space_groups

    def test_without_a_plan_the_space_stays_one_block(self):
        """没有实测分段（老草稿、PDF 路线）就退回整块留白。"""
        self.assertEqual(self.groups(30, None), [{"mm": 30.0, "continuation": False}])

    def test_a_plan_whose_total_does_not_match_is_refused(self):
        """量来的总高跟这次要的留白对不上 —— 那是别的卷子/别的设置，不能拿来排。"""
        groups = self.groups(30, [{"mm": 12.0, "continuation": False}])
        self.assertEqual(groups, [{"mm": 30.0, "continuation": False}])

    def test_a_valid_plan_keeps_its_pieces(self):
        """对得上的分段照单全收，续页那几段要记住是续页。"""
        plan = [{"mm": 24.6, "continuation": False}, {"mm": 5.4, "continuation": True}]
        groups = self.groups(30, plan)
        self.assertEqual(len(groups), 2)
        self.assertFalse(groups[0]["continuation"])
        self.assertTrue(groups[1]["continuation"])

    def test_a_plan_with_a_broken_piece_is_refused(self):
        """坏值不能进：宁可退回整块，也不能排出一份错的卷子。"""
        self.assertEqual(self.groups(30, [{"mm": "abc", "continuation": False}]), [{"mm": 30.0, "continuation": False}])

    def test_a_plan_without_pieces_is_refused(self):
        """空计划等于没量。"""
        self.assertEqual(self.groups(30, []), [{"mm": 30.0, "continuation": False}])


class ContinuationLabelTests(SimpleTestCase):
    """续页上必须有一行「第 N 题答题区（续）」。"""

    def setUp(self):
        self.document = build_document(
            splits={QUESTION_ID: [{"mm": 24.6, "continuation": False}, {"mm": 5.4, "continuation": True}]}
        )
        self.labels = continuation_labels(self.document)

    def test_the_continuation_page_carries_a_heading(self):
        """续页上那行提示必须写出来。"""
        self.assertEqual(len(self.labels), 1, "续页上没有「第 N 题答题区（续）」，学生不知道那块白是哪道题的")

    def test_the_heading_names_the_question(self):
        """标题里是题号。"""
        self.assertEqual(text_of(self.labels[0]), "第 1 题答题区（续）")

    def test_the_heading_uses_the_preview_font_size(self):
        """预览是 10px = 7.5 磅（w:sz 以半磅为单位）。"""
        size = self.labels[0].find(".//" + qn("w:sz"))
        self.assertIsNotNone(size, "标题没有字号")
        self.assertEqual(int(size.get(qn("w:val"))), 15)

    def test_the_heading_uses_the_preview_colour(self):
        """预览是 #666。"""
        colour = self.labels[0].find(".//" + qn("w:color"))
        self.assertIsNotNone(colour, "标题没有颜色")
        self.assertEqual(colour.get(qn("w:val")), PREVIEW_LABEL_COLOR)

    def test_the_heading_uses_the_preview_line_box(self):
        """行盒 13.5px = 10.125 磅（固定行高，Word 才不会把它撑高）。"""
        spacing = spacing_of(self.labels[0])
        self.assertEqual(spacing.get(qn("w:lineRule")), "exact")
        millimetres = twips(spacing, "line") / 20 * 25.4 / 72
        expected = PREVIEW_LABEL_LINE_BOX_PX * 0.75 * 25.4 / 72
        self.assertAlmostEqual(millimetres, expected, delta=0.01)

    def test_the_heading_keeps_the_preview_bottom_margin(self):
        """下外边距 4px = 3 磅。"""
        spacing = spacing_of(self.labels[0])
        self.assertEqual(twips(spacing, "after"), PREVIEW_LABEL_AFTER_PX * 15)

    def test_the_heading_stays_with_the_writing_space_below_it(self):
        """标题必须跟住下面那块续页留白 —— 不然它会留在上一页页底。"""
        self.assertTrue(self.labels, "续页上根本没有标题，这条无从检查")
        for label in self.labels:
            self.assertTrue(
                keep_next_value(label), "续页标题没有 keepNext：Word 可能把它留在上一页页底，续页上就没提示了"
            )

    def test_the_heading_sits_directly_above_the_continuation_space(self):
        """标题后面紧跟着的就是续页那块留白。"""
        body = list(self.document._element.body)
        index = body.index(self.labels[0])
        following = body[index + 1]
        spacing = spacing_of(following)
        self.assertIsNotNone(spacing, "标题后面不是留白段")
        self.assertEqual(spacing.get(qn("w:lineRule")), "exact")


class SplitHeightTests(SimpleTestCase):
    """照着预览的分段写，高度必须守恒，粒度必须还在 20.1 毫米。"""

    def setUp(self):
        self.document = build_document(
            splits={QUESTION_ID: [{"mm": 24.6, "continuation": False}, {"mm": 5.4, "continuation": True}]}
        )
        self.segments = answer_space_segments(self.document)

    def test_the_two_pieces_add_up_to_the_requested_space(self):
        """首页那份 + 续页那份 = 30 毫米。"""
        total = sum(twips(spacing_of(segment), "line") for segment in self.segments)
        self.assertAlmostEqual(total, THIRTY_MM_TWIPS, delta=2, msg=f"留白总高 {total} twips，不是 30 毫米")

    def test_no_segment_is_taller_than_the_preview_minimum_writable(self):
        """段高就是 Word「整段走」的阈值，不能超过 20.1 毫米。"""
        for segment in self.segments:
            line = twips(spacing_of(segment), "line")
            self.assertLessEqual(
                line,
                PREVIEW_MIN_WRITABLE_TWIPS,
                f"有一段留白 {line} twips（{line * 25.4 / 1440:.1f} 毫米），比预览的 MIN_WRITABLE 还高",
            )

    def test_the_home_piece_is_split_at_the_preview_threshold(self):
        """首页那份（24.6 毫米）切成 20.1 + 4.5 —— 切点跟预览一致。"""
        lines = [twips(spacing_of(segment), "line") for segment in self.segments]
        self.assertEqual(lines[0], PREVIEW_MIN_WRITABLE_TWIPS, f"首页那一份的头一段不是 20.1 毫米：{lines}")
        self.assertGreater(len(lines), 2, "首页那份和续页那份没有分开写")

    def test_the_continuation_piece_is_written_after_the_heading(self):
        """续页那一份只有 5.4 毫米，它前面是标题，不跟首页那份混在一起。

        判据是**精确高度**：续页那份必须正好是预览量出来的 306 twips。
        只判「比一半小」太弱 —— 整块 30 毫米退化成 20.1 + 9.9 时，
        末段 561 twips 也比一半小，但它并不是预览那一段。
        """
        lines = [twips(spacing_of(segment), "line") for segment in self.segments]
        self.assertEqual(lines[-1], CONTINUATION_PIECE_TWIPS, f"续页那一份不是预览量出来的高度：{lines}")


class WithoutPlanTests(SimpleTestCase):
    """没有实测分段时，老行为不能变（更不能凭空多出标题）。"""

    def test_no_heading_is_written_without_a_plan(self):
        document = build_document(splits=None)
        self.assertEqual(continuation_labels(document), [], "没有分段计划却写出了续页标题")

    def test_the_space_is_still_split_for_the_pagination_threshold(self):
        """没有分段计划也照样切段 —— 那是分页点对齐的前提（第七十轮）。"""
        document = build_document(splits=None)
        segments = answer_space_segments(document)
        self.assertGreaterEqual(len(segments), 2)
        total = sum(twips(spacing_of(segment), "line") for segment in segments)
        self.assertEqual(total, THIRTY_MM_TWIPS)

    def test_a_plan_whose_total_does_not_match_writes_no_heading(self):
        """对不上的计划当没量过：不写标题，也不按它切。"""
        document = build_document(splits={QUESTION_ID: [{"mm": 12.0, "continuation": False}]})
        self.assertEqual(continuation_labels(document), [])
        segments = answer_space_segments(document)
        total = sum(twips(spacing_of(segment), "line") for segment in segments)
        self.assertEqual(total, THIRTY_MM_TWIPS)


class MovedBlockTests(SimpleTestCase):
    """整块挪到续页的题（首页没有留白，续页才是整块）。"""

    def setUp(self):
        self.document = build_document(splits={QUESTION_ID: [{"mm": 30.0, "continuation": True}]})
        self.labels = continuation_labels(self.document)

    def test_the_heading_is_written_before_the_whole_block(self):
        """首页一点留白都没有，续页上整块留白前面要有标题。"""
        self.assertEqual(len(self.labels), 1, "整块挪到续页的题没有写标题")
        body = list(self.document._element.body)
        index = body.index(self.labels[0])
        spacing = spacing_of(body[index + 1])
        self.assertEqual(spacing.get(qn("w:lineRule")), "exact")

    def test_the_block_keeps_its_full_height(self):
        """整块还是 30 毫米。"""
        segments = answer_space_segments(self.document)
        total = sum(twips(spacing_of(segment), "line") for segment in segments)
        self.assertEqual(total, THIRTY_MM_TWIPS)


class PrintOptionsTests(SimpleTestCase):
    """白名单：这个键得被认下来，坏值得被挡住。"""

    def test_the_option_is_accepted(self):
        options = library_drafts._print_options(
            {"measured_answer_splits": {QUESTION_ID: [{"mm": 24.6, "continuation": False}]}}, ids=[QUESTION_ID]
        )
        self.assertEqual(options["measured_answer_splits"], {QUESTION_ID: [{"mm": 24.6, "continuation": False}]})

    def test_a_non_numeric_height_is_refused(self):
        with self.assertRaises(library_drafts.DraftError):
            library_drafts._print_options(
                {"measured_answer_splits": {QUESTION_ID: [{"mm": "24.6"}]}}, ids=[QUESTION_ID]
            )

    def test_a_zero_or_negative_height_is_refused(self):
        with self.assertRaises(library_drafts.DraftError):
            library_drafts._print_options({"measured_answer_splits": {QUESTION_ID: [{"mm": 0}]}}, ids=[QUESTION_ID])

    def test_a_height_beyond_the_page_is_refused(self):
        with self.assertRaises(library_drafts.DraftError):
            library_drafts._print_options({"measured_answer_splits": {QUESTION_ID: [{"mm": 500}]}}, ids=[QUESTION_ID])

    def test_a_non_boolean_continuation_flag_is_refused(self):
        with self.assertRaises(library_drafts.DraftError):
            library_drafts._print_options(
                {"measured_answer_splits": {QUESTION_ID: [{"mm": 30, "continuation": "yes"}]}}, ids=[QUESTION_ID]
            )

    def test_a_broken_question_id_is_refused(self):
        with self.assertRaises(library_drafts.DraftError):
            library_drafts._print_options({"measured_answer_splits": {"not-a-uuid": [{"mm": 30}]}}, ids=[QUESTION_ID])

    def test_a_question_outside_the_selection_is_refused(self):
        with self.assertRaises(library_drafts.DraftError):
            library_drafts._print_options({"measured_answer_splits": {OTHER_ID: [{"mm": 30}]}}, ids=[QUESTION_ID])

    def test_an_unknown_key_is_refused(self):
        """白名单不能因为多了这个键就放松。"""
        with self.assertRaises(library_drafts.DraftError):
            library_drafts._print_options({"measured_answer_splits_x": {}}, ids=[QUESTION_ID])

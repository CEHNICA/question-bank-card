"""第七十轮守护：导出 Word 的答题留白必须**能跨页断开**。

实测出来的缺陷（探针 _probe133.py，40 题对照卷，真实 Word 导出）：

    预览每页首题   1, 7, 14, 20, 27, 33, 40
    Word  每页首题 1, 7, 13, 19, 25, 31, 37     ← 7 页里 5 页对不上

根因不是「每题占高差」（两边中位数只差 0.081 毫米），而是**答题留白不可断开**：

    预览（frontend/exam-layout.js 的 writingSpace）把留白当**可切**的东西 ——
    页底放不下的那几毫米挪到下一页，续页顶部还打一行「第 N 题答题区（续）」。
    Word 原来把 30 毫米留白写成**一整块**固定行高的空段：固定行高的段落不可
    断开，放不下就整题推到下一页，于是 Word 每页比预览少装一题，分页点逐页漂。

修法：留白切成若干个**单行**固定行高段。单行段落 Word 没法在段内断开，
只能整段挪到下一页 —— 「页底放得下就留几毫米、放不下就整段走」跟预览对上了。

段高上限取 **1140 twips = 57 磅 = 20.1 毫米**，不是随手取的数：预览里留白块
往下切的时候有一条阈值（exam-layout.js 的 MIN_WRITABLE = 76px = 20.1 毫米）——
页底剩下不足 20.1 毫米就**整块**把留白推到下一页，不留碎片。Word 单行段的
「放不下就整段走」阈值正好等于段高，所以段高取 20.1 毫米，两边的规则就对上了。

判据全是独立字面值（预览的 CSS 像素 + 毫米换算），不引用被测模块的常量。
"""

import io

from django.test import SimpleTestCase
from docx.oxml.ns import qn

STEM_QUESTION = "33333333-3333-4333-8333-333333333333"

# 预览 exam-layout.js 的 writingSpace：MIN_WRITABLE = 76（CSS 像素）。
PREVIEW_MIN_WRITABLE_PX = 76
# 76 像素 = 57 磅 = 1140 twips = 20.108 毫米。
PREVIEW_MIN_WRITABLE_TWIPS = 1140
# 毫米换算（1 毫米 = 1440/25.4 twips）。
THIRTY_MM_TWIPS = 1701
SIXTY_MM_TWIPS = 3402
# 题块下外边距（预览 .print-question 的 margin-bottom）。
QUESTION_GAP_TWIPS = 160


def build_item(question_id="33333333-3333-4333-8333-333333333333", stem="题干示例。"):
    return {
        "id": question_id,
        "type": "free_response",
        "where": "第 1 题",
        "content": {"options": {}, "body_mode": "text"},
        "fields": {"stem": [{"type": "text", "segments": [{"type": "text", "raw": stem}]}]},
        "images": [],
    }


def build_document(items, answer_space="auto", font_size=12):
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
    data = library_export._document(items, "第七十轮", options, "questions")
    from docx import Document

    return Document(io.BytesIO(data))


def spacing_of(element):
    return element.find(qn("w:pPr") + "/" + qn("w:spacing"))


def twips(spacing, name):
    value = spacing.get(qn(f"w:{name}"))
    return int(value) if value is not None else None


def answer_space_segments(document):
    """导出结果里所有「固定行高的空段」—— 留白块的一段。"""
    found = []
    for element in document._element.body:
        if element.tag != qn("w:p"):
            continue
        spacing = spacing_of(element)
        if spacing is None or spacing.get(qn("w:lineRule")) != "exact":
            continue
        if "".join(node.text or "" for node in element.findall(".//" + qn("w:t"))).strip():
            continue
        found.append(element)
    return found


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
    """尺子自检：预览的阈值换算成 Word 的 twips。"""

    def test_the_preview_minimum_writable_is_twenty_millimetres(self):
        """MIN_WRITABLE = 76px = 57 磅 = 1140 twips。"""
        points = PREVIEW_MIN_WRITABLE_PX * 0.75
        self.assertAlmostEqual(points, 57, places=3)
        self.assertEqual(round(points * 20), PREVIEW_MIN_WRITABLE_TWIPS)

    def test_the_thirty_millimetre_space_needs_more_than_one_segment(self):
        """30 毫米装不进一段（1140 twips = 20.1 毫米），所以必须切。"""
        self.assertGreater(THIRTY_MM_TWIPS, PREVIEW_MIN_WRITABLE_TWIPS)


class BreakableAnswerSpaceTests(SimpleTestCase):
    """留白必须切段，否则 Word 放不下就整题推走，分页点全乱。"""

    def setUp(self):
        self.document = build_document([build_item()], answer_space=30)
        self.segments = answer_space_segments(self.document)

    def test_the_answer_space_is_split_into_several_segments(self):
        """30 毫米留白写成多段，Word 才能在页底把它切开。"""
        self.assertGreaterEqual(len(self.segments), 2, "30 毫米的留白还是一整块，Word 放不下就整题推走")

    def test_no_segment_is_taller_than_the_preview_minimum_writable(self):
        """每段不高于 20.1 毫米 —— 段高就是 Word「整段走」的阈值。"""
        for segment in self.segments:
            line = twips(spacing_of(segment), "line")
            self.assertLessEqual(
                line,
                PREVIEW_MIN_WRITABLE_TWIPS,
                f"有一段留白 {line} twips（{line * 25.4 / 1440:.1f} 毫米），比预览的 MIN_WRITABLE 还高",
            )

    def test_one_segment_reaches_the_preview_minimum_writable(self):
        """至少有一段正好是 20.1 毫米 —— 段高阈值真的取在 MIN_WRITABLE 上。

        取小了，Word 会在页底留下预览不会留的碎片；取大了，Word 会把本该留在
        本页的留白整块推到下一页。
        """
        lines = [twips(spacing_of(segment), "line") for segment in self.segments]
        self.assertIn(PREVIEW_MIN_WRITABLE_TWIPS, lines, f"没有一段等于 1140 twips：{lines}")

    def test_the_segments_keep_the_requested_height(self):
        """高度守恒：各段之和还是老师要的 30 毫米。"""
        total = sum(twips(spacing_of(segment), "line") for segment in self.segments)
        self.assertEqual(total, THIRTY_MM_TWIPS, f"留白总高 {total} twips，不是 30 毫米")

    def test_the_segments_are_not_bound_together_with_keep_next(self):
        """留白段之间不能有 keepNext —— keepNext 会把整组钉在一页，跨页就废了。"""
        for segment in self.segments:
            self.assertFalse(keep_next_value(segment), "留白段被 keepNext 绑住了，Word 不许把它挪到下一页")

    def test_the_segments_have_no_extra_spacing(self):
        """段与段之间不留缝（预览的留白是一整块连续的白）。"""
        for segment in self.segments[:-1]:
            spacing = spacing_of(segment)
            self.assertEqual(twips(spacing, "before") or 0, 0, "留白段上面多了外边距")
            self.assertEqual(twips(spacing, "after") or 0, 0, "留白段下面多了外边距")

    def test_the_last_segment_carries_the_question_gap(self):
        """留白之后的 8 磅题块下外边距落在最后一段上。"""
        spacing = spacing_of(self.segments[-1])
        self.assertEqual(twips(spacing, "after"), QUESTION_GAP_TWIPS)


class LongerAnswerSpaceTests(SimpleTestCase):
    """更长的留白也要切段，而且高度照样守恒。"""

    def test_sixty_millimetres_is_split_and_keeps_its_height(self):
        document = build_document([build_item()], answer_space=60)
        segments = answer_space_segments(document)
        self.assertGreaterEqual(len(segments), 2, "60 毫米的留白没有被切开")
        total = sum(twips(spacing_of(segment), "line") for segment in segments)
        self.assertEqual(total, SIXTY_MM_TWIPS, f"留白总高 {total} twips，不是 60 毫米")

    def test_a_short_space_stays_one_segment(self):
        """8 毫米留白装得下一段，就不该切（切了也是白切）。"""
        document = build_document([build_item()], answer_space=8)
        segments = answer_space_segments(document)
        self.assertEqual(len(segments), 1, "8 毫米的留白不该被切开")


class PaginationEstimateTests(SimpleTestCase):
    """分页估算也得按「各段之和」算留白。"""

    def test_the_estimated_height_matches_the_requested_space(self):
        from . import library_export

        document = build_document([build_item()], answer_space=30)
        segments = answer_space_segments(document)
        measured = sum(library_export._paragraph_measure(segment, 12) for segment in segments)
        # 留白 30 毫米 + 落在最后一段上的 8 磅题块下外边距。
        expected = 30.0 + QUESTION_GAP_TWIPS / 20 * 25.4 / 72
        self.assertAlmostEqual(
            measured,
            expected,
            delta=0.05,
            msg=f"分页估算里 30 毫米留白占 {measured:.3f} 毫米，应为 {expected:.3f} 毫米",
        )

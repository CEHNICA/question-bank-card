"""导出 Word 的页脚：照抄打印预览的页脚。

预览真值（frontend/exam-layout.js:463 拼文字，frontend/library.css:901 定样式；
下面这些数字是 Chrome 实测 + 真实预览 PDF 里量出来的，不是估的）：

    文字   第 1 页 / 共 2 页 · 共 1 题
           —— 页码在前、题数在后，中间「/」两侧各一个半角空格，
              题数前面用 U+00B7 中点加一个半角空格
    字号   10px = 7.5 磅
    颜色   #666666
    字体   Microsoft YaHei
    对齐   居中

Word 侧原来写的是「共 N 题 · 第 {PAGE} 页」：页码和题数顺序反了、分隔符反了、
没有总页数、字号 9 磅、纯黑、右对齐 —— 整行跟预览不是一回事。

这里锁的是「写对了没有」。域（PAGE / NUMPAGES）的结果由 Word 打开时才算出来，
算出来对不对由真实 Word 渲染的尺子验（_probe92.py / _audit41.py）。
"""

import io

from django.test import SimpleTestCase

from . import library_export

# 预览真值（独立写死，不引用被测模块的常量）
PREVIEW_TEXT_TEMPLATE = "第 {page} 页 / 共 {total} 页 · 共 {count} 题"
PREVIEW_FONT_SIZE_PT = 7.5
PREVIEW_COLOR = "666666"
PREVIEW_FONT_FAMILY = "Microsoft YaHei"


def item(index, kind="single_choice"):
    return {
        "id": f"{index:08d}-1111-4111-8111-111111111111",
        "type": kind,
        "where": f"第 {index} 题",
        "content": {},
        "fields": {"stem": [{"type": "text", "segments": [{"type": "text", "raw": f"第{index}题的题干"}]}]},
        "images": [],
    }


def captured(count):
    return [item(index + 1) for index in range(count)]


def options(**overrides):
    base = {
        "font_size": 12,
        "student_info": False,
        "answer_space": 0,
        "answer_space_overrides": {},
        "question_breaks": [],
        "pagination": "compact",
        "answer_layout": "appendix",
        "figure_overrides": {},
        "measured_figure_widths": {},
    }
    base.update(overrides)
    return base


def build(count=3, **option_overrides):
    return library_export._document(captured(count), "导出体检卷", options(**option_overrides), "questions")


def footer_paragraph(blob):
    from docx import Document

    return Document(io.BytesIO(blob)).sections[0].footer.paragraphs[0]


def footer_text_with_fields(blob):
    """把页脚拼成「文字 + 域占位」的样子：第 <PAGE> 页 / 共 <NUMPAGES> 页 · 共 3 题。

    python-docx 的 paragraph.text 只给直接子级的 run，域里的文字取不到，
    所以这里按 XML 顺序自己走一遍。
    """
    from docx.oxml.ns import qn

    paragraph = footer_paragraph(blob)
    parts = []
    for child in paragraph._p:
        if child.tag == qn("w:r"):
            parts.append("".join(node.text or "" for node in child.findall(qn("w:t"))))
        elif child.tag == qn("w:fldSimple"):
            parts.append("<" + (child.get(qn("w:instr")) or "") + ">")
    return "".join(parts)


def text_runs(blob):
    """页脚里的普通文字 run（不含域里的）。"""
    from docx.oxml.ns import qn

    paragraph = footer_paragraph(blob)
    out = []
    for child in paragraph._p:
        if child.tag == qn("w:r"):
            out.append(child)
    return out


def field_runs(blob):
    """域里的 run（Word 更新域时按这里的格式重排）。"""
    from docx.oxml.ns import qn

    paragraph = footer_paragraph(blob)
    out = []
    for field in paragraph._p.findall(qn("w:fldSimple")):
        out.extend(field.findall(qn("w:r")))
    return out


def run_size_pt(element):
    from docx.text.run import Run

    size = Run(element, None).font.size
    return None if size is None else size.pt


def run_color(element):
    from docx.text.run import Run

    color = Run(element, None).font.color
    return None if color is None or color.rgb is None else str(color.rgb)


def run_font_name(element):
    from docx.text.run import Run

    return Run(element, None).font.name


def footer_style(blob):
    from docx import Document

    return Document(io.BytesIO(blob)).styles["Footer"]


class FooterPreviewTests(SimpleTestCase):
    def test_the_footer_text_follows_the_preview_order(self):
        """整行文字照抄预览：页码在前、总页数其次、题数最后。"""
        expected = PREVIEW_TEXT_TEMPLATE.format(page="<PAGE>", total="<NUMPAGES>", count=3)
        self.assertEqual(expected, footer_text_with_fields(build(count=3)))

    def test_the_total_page_count_is_a_real_field(self):
        """总页数必须是 NUMPAGES 域 —— 分完页才知道几页，写死就一定错。"""
        from docx.oxml.ns import qn

        paragraph = footer_paragraph(build(count=2))
        fields = [f.get(qn("w:instr")) for f in paragraph._p.findall(qn("w:fldSimple"))]
        self.assertEqual(fields.count("NUMPAGES"), 1, "页脚里应当正好有一个 NUMPAGES 域")

    def test_the_footer_is_centred(self):
        """预览 .exam-page-number 是 text-align:center，不是右对齐。"""
        paragraph = footer_paragraph(build(count=2))
        self.assertEqual(int(paragraph.alignment), 1, "页脚应当居中对齐")

    def test_the_footer_font_size_matches_the_preview(self):
        """预览 10px = 7.5 磅。"""
        sizes = {run_size_pt(run) for run in text_runs(build(count=2))}
        self.assertEqual(sizes, {PREVIEW_FONT_SIZE_PT})

    def test_the_field_result_uses_the_preview_font_size(self):
        """域里的 run 也要带字号 —— 域更新时按它的格式重排。"""
        sizes = {run_size_pt(run) for run in field_runs(build(count=2))}
        self.assertEqual(sizes, {PREVIEW_FONT_SIZE_PT})

    def test_the_footer_colour_matches_the_preview(self):
        """预览是 #666666，不是纯黑。"""
        colors = {run_color(run) for run in text_runs(build(count=2))}
        self.assertEqual(colors, {PREVIEW_COLOR})

    def test_the_field_result_uses_the_preview_colour(self):
        """域里的 run 也要带颜色。"""
        colors = {run_color(run) for run in field_runs(build(count=2))}
        self.assertEqual(colors, {PREVIEW_COLOR})

    def test_the_footer_font_family_matches_the_preview(self):
        """预览是 Microsoft YaHei。"""
        names = {run_font_name(run) for run in text_runs(build(count=2))}
        self.assertEqual(names, {PREVIEW_FONT_FAMILY})

    def test_the_footer_paragraph_uses_the_footer_style(self):
        """页脚段落必须引用 Footer 样式 —— 域结果就是按这个样式重排的。"""
        paragraph = footer_paragraph(build(count=2))
        self.assertEqual(paragraph.style.name, "Footer")

    def test_the_footer_style_font_size_matches_the_preview(self):
        """域结果的字号取自 Footer 样式，不取自域里的 run。

        真 Word 渲染实测（_probe96.py 五个变体）：fldSimple 里带格式的 run、
        段落标记的 rPr、三段式域都管不住域结果，只有改样式才管用。
        """
        style = footer_style(build(count=2))
        self.assertEqual(style.font.size.pt, PREVIEW_FONT_SIZE_PT)

    def test_the_footer_style_colour_matches_the_preview(self):
        """域结果的颜色同样取自 Footer 样式。"""
        style = footer_style(build(count=2))
        self.assertEqual(str(style.font.color.rgb), PREVIEW_COLOR)

    def test_the_footer_style_font_family_matches_the_preview(self):
        """域结果的字体同样取自 Footer 样式。"""
        style = footer_style(build(count=2))
        self.assertEqual(style.font.name, PREVIEW_FONT_FAMILY)

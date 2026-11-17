"""字体规格：字号 / 字重 / 字体族，Word 必须跟打印预览一模一样。

预览（frontend/library.css、frontend/styles.css）里的字体规格是这么定的：

    .print-title    { font-size: 24px; font-family: var(--content-font) }
        → 24px = 18 磅；--content-font 的西文首选是 Times New Roman，中文是宋体
    .print-section  { font-size: 16px; font-family: "SimHei","Microsoft YaHei",sans-serif }
        → 16px = 12 磅；中文走黑体
    .qb-number      { font-weight: 700 }     → 题号是粗的
    .solution-label { font-weight: 600 }     → 「答案：」「解析：」是半粗

Word 当年对着这套另定了一套：小标题用「字号选项 + 1」（13.2 磅）、中文一律宋体、
题号和标签全是常规字重、西文用 Cambria。纸和屏幕上的字根本不是一套字 —— 老师在
预览里看到的和印在卷子上的对不上。

这里锁六件事（判据一律写独立字面值，不引用被测模块的常量 —— 否则反向变体把
常量一改，这条守护会跟着一起放绿）：

1. 小标题 12 磅（预览的 16px），中文黑体，加粗；
2. 大标题 18 磅（预览的 24px），加粗；
3. 两个标题的字号是定值，不跟「字号选项」走（预览里它们就是写死的 px）；
4. 「答案：」「解析：」标签加粗；
5. 题干题号、答案块题号都加粗；
6. 西文走 Times New Roman（预览 --content-font 的首选）。

字重上 Word 只有「粗 / 不粗」两档，预览的半粗 600 只能落到粗（700）—— 这是
Word 的能力上限，属于已知近似。
"""

import io
from pathlib import Path

from django.test import SimpleTestCase

from . import library_drafts, library_export as export

UID1 = "11111111-2222-3333-4444-555555555555"
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REPO = Path(__file__).resolve().parents[2]

# 预览侧的字面值（frontend/library.css、frontend/styles.css）：
#   .print-title   { font-size: 24px }                       → 18 磅
#   .print-section { font-size: 16px; font-family: SimHei }   → 12 磅、中文黑体
#   --content-font 首选 "Times New Roman"
PREVIEW_TITLE_PT = 18.0
PREVIEW_SECTION_PT = 12.0
PREVIEW_SECTION_EAST_ASIA = "黑体"
PREVIEW_LATIN_FONT = "Times New Roman"
# .print-title { letter-spacing: .12em } → Word 里对应 w:spacing（1/20 磅）。
# 实测 30 这一档的标题宽度最接近预览（181.42 磅 vs 预览 179.28 磅）。
PREVIEW_TITLE_LETTER_SPACING = 30


def _item(uid="11111111-2222-3333-4444-555555555555", where="第 1 题"):
    return {
        "id": uid,
        "where": where,
        "type": "free_response",
        "content": {"stem": "看图作答。", "options": {}, "origin": ""},
        "extras": {},
        "selected": {"answer": "答案是 42。", "analysis": "解析如下。"},
        "ai": False,
        "hash": "",
        "fields": {
            "stem": [{"type": "text", "segments": [{"type": "text", "raw": "看图作答。"}]}],
            "answer": [{"type": "text", "segments": [{"type": "text", "raw": "答案是 42。"}]}],
            "analysis": [{"type": "text", "segments": [{"type": "text", "raw": "解析如下。"}]}],
        },
        "images": [],
        "solution_images": [],
        "solution_id": None,
        "selection_revision": None,
        "solution_explicit": False,
    }


def _runs(document="combined", font_size=None, title="字体规格对照"):
    """导出成 docx，把正文里每一个 run 的字体规格读出来。"""
    value = {"document": document, "student_info": False}
    if font_size is not None:
        value["font_size"] = font_size
    options = library_drafts._print_options(value, {"answer_layout": "appendix"}, ids=[UID1])
    payload = export._document([_item()], title, options, options["document"])
    from docx import Document

    doc = Document(io.BytesIO(payload))
    out = []
    for element in doc.element.body.iter(W_NS + "r"):
        pieces = []
        for node in element:
            if node.tag == W_NS + "t":
                pieces.append(node.text or "")
            elif node.tag == W_NS + "tab":
                pieces.append("\t")
        text = "".join(pieces)
        rpr = element.find(W_NS + "rPr")
        size = None
        bold = False
        east_asia = None
        latin = None
        spacing = None
        if rpr is not None:
            node = rpr.find(W_NS + "sz")
            if node is not None:
                size = int(node.get(W_NS + "val")) / 2.0
            node = rpr.find(W_NS + "b")
            if node is not None:
                bold = node.get(W_NS + "val") not in ("0", "false")
            node = rpr.find(W_NS + "rFonts")
            if node is not None:
                east_asia = node.get(W_NS + "eastAsia")
                latin = node.get(W_NS + "ascii")
            node = rpr.find(W_NS + "spacing")
            if node is not None:
                spacing = int(node.get(W_NS + "val"))
        out.append(
            {
                "text": text,
                "size": size,
                "bold": bold,
                "east_asia": east_asia,
                "latin": latin,
                "letter_spacing": spacing,
            }
        )
    return out


def _find(runs, text):
    hits = [run for run in runs if run["text"] == text]
    if len(hits) != 1:
        raise AssertionError(f"run {text!r} 应该只出现一次，实际 {len(hits)} 次")
    return hits[0]


class FontSpecTests(SimpleTestCase):
    def test_answer_section_heading_uses_the_preview_font_size(self):
        run = _find(_runs(), "参考答案与解析")
        self.assertEqual(run["size"], PREVIEW_SECTION_PT)

    def test_answer_section_heading_uses_the_preview_font_family(self):
        run = _find(_runs(), "参考答案与解析")
        self.assertEqual(run["east_asia"], PREVIEW_SECTION_EAST_ASIA)

    def test_answer_section_heading_is_bold(self):
        run = _find(_runs(), "参考答案与解析")
        self.assertTrue(run["bold"])

    def test_document_title_uses_the_preview_font_size(self):
        run = _find(_runs(), "字体规格对照")
        self.assertEqual(run["size"], PREVIEW_TITLE_PT)

    def test_document_title_is_bold(self):
        run = _find(_runs(), "字体规格对照")
        self.assertTrue(run["bold"])

    def test_document_title_keeps_the_preview_letter_spacing(self):
        """预览的标题是散开排的（letter-spacing: .12em），Word 这边原来一点没有。"""
        run = _find(_runs(), "字体规格对照")
        self.assertEqual(run["letter_spacing"], PREVIEW_TITLE_LETTER_SPACING)

    def test_heading_sizes_do_not_follow_the_font_size_option(self):
        """预览的 .print-title / .print-section 是写死的 px，不跟 --exam-font-size 走。

        Word 原来用「字号选项 + 6 / + 1」，老师把字号调到 16 磅，纸上的标题就跟着
        变大了 —— 屏幕上还是那么大的字，纸上却不是同一套。
        """
        runs = _runs(font_size=16)
        self.assertEqual(_find(runs, "字体规格对照")["size"], PREVIEW_TITLE_PT)
        self.assertEqual(_find(runs, "参考答案与解析")["size"], PREVIEW_SECTION_PT)

    def test_answer_label_is_bold(self):
        run = _find(_runs(), "答案：")
        self.assertTrue(run["bold"])

    def test_analysis_label_is_bold(self):
        run = _find(_runs(), "解析：")
        self.assertTrue(run["bold"])

    def test_stem_question_number_is_bold(self):
        runs = _runs()
        numbers = [run for run in runs if run["text"].strip() == "1."]
        self.assertTrue(numbers, [run["text"] for run in runs])
        self.assertTrue(all(run["bold"] for run in numbers))

    def test_body_text_uses_the_preview_latin_font(self):
        runs = _runs()
        bodies = [run for run in runs if "看图作答" in run["text"] or "解析如下" in run["text"]]
        self.assertTrue(bodies)
        for run in bodies:
            with self.subTest(text=run["text"]):
                self.assertEqual(run["latin"], PREVIEW_LATIN_FONT)


class PreviewFontBaselineTests(SimpleTestCase):
    """锁住预览侧那一套基准：预览改了，Word 这边要跟着改。"""

    def setUp(self):
        self.library_css = (REPO / "frontend" / "library.css").read_text(encoding="utf-8")
        self.styles_css = (REPO / "frontend" / "styles.css").read_text(encoding="utf-8")

    def test_print_title_is_24px(self):
        import re

        block = re.search(r"\.print-title\s*\{[^}]*\}", self.library_css)
        self.assertIsNotNone(block, "找不到 .print-title")
        self.assertIn("font-size: 24px", block.group(0))

    def test_print_section_is_16px_in_a_hei(self):
        import re

        block = re.search(r"\.print-section\s*\{[^}]*\}", self.library_css)
        self.assertIsNotNone(block, "找不到 .print-section")
        self.assertIn("font-size: 16px", block.group(0))
        self.assertIn("SimHei", block.group(0))

    def test_solution_label_is_semi_bold(self):
        import re

        block = re.search(r"\.solution-label\s*\{[^}]*\}", self.library_css)
        self.assertIsNotNone(block, "找不到 .solution-label")
        self.assertIn("font-weight: 600", block.group(0))

    def test_question_number_is_bold(self):
        import re

        block = re.search(r"\.qb-number\s*\{[^}]*\}", self.styles_css)
        self.assertIsNotNone(block, "找不到 .qb-number")
        self.assertIn("font-weight: 700", block.group(0))

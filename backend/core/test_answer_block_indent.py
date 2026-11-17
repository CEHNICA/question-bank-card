"""答案块在 Word 里必须和打印预览排成同一种两栏。

预览里答案块是「题号列 + 正文列」两栏网格：

    .print-answer-row { display: grid; grid-template-columns: 2.4em minmax(0, 1fr); gap: 4px; }

老师核对的是屏幕：题干顶格，答案正文、解析文字、解析配图都缩在题号右边那一列
（实测缩进 11.22mm、那一列净宽 166.78mm）。Word 当年把「1. 答案：……」整句顶格写，
解析配图也从版心左缘开始 —— 纸上和屏幕上对不上。

这里锁三件事：

1. 答案正文缩进到那一列，题号用悬挂缩进顶格；
2. 解析配图不宽于那一列的净宽；
3. Word 侧那两个常量跟样式表里那条网格对得上（样式表改了，Word 必须跟着改）。

``w:ind`` 的单位是 twips（1/1440 英寸），不是 EMU —— 量的时候别拿 EMU 除。
"""

import io
import re
from pathlib import Path

from django.test import SimpleTestCase

from . import library_drafts, library_export as export

UID = "11111111-2222-3333-4444-555555555555"
CSS_PATH = Path(__file__).resolve().parents[2] / "frontend" / "library.css"

# 预览那条网格的字面值：2.4em 题号列 + 4px 间隙，字号 12pt = 16px。
EXPECTED_INDENT_MM = round((2.4 * 16 + 4) * 25.4 / 96, 2)
EXPECTED_COLUMN_MM = round(178.0 - EXPECTED_INDENT_MM, 2)


def _png(width, height):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def _captured(answer="答案是 42。", analysis="解析如下。", size=(400, 500), display_width=178.0):
    data = _png(*size)
    return [
        {
            "id": UID,
            "where": "第 1 题",
            "type": "free_response",
            "content": {"stem": "看图作答。", "options": {}, "origin": ""},
            "extras": {},
            "selected": {"answer": answer, "analysis": analysis},
            "ai": False,
            "hash": "",
            "fields": {
                "stem": [{"type": "text", "segments": [{"type": "text", "raw": "看图作答。"}]}],
                "answer": [{"type": "text", "segments": [{"type": "text", "raw": answer}]}],
                "analysis": [{"type": "text", "segments": [{"type": "text", "raw": analysis}]}],
            },
            "images": [],
            "solution_images": [
                {
                    "bytes": data,
                    "size": list(size),
                    "display_width": display_width,
                    "position": "after",
                    "paragraph": 0,
                    "name": "solution-1.png",
                }
            ],
            "solution_id": None,
            "selection_revision": None,
            "solution_explicit": False,
        }
    ]


def _export(captured, options=None):
    options = options or library_drafts._print_options(
        {"document": "answers", "student_info": False}, {"answer_layout": "appendix"}, ids=[UID]
    )
    payload = export._document(captured, "答案块缩进", options, options["document"])
    from docx import Document

    return Document(io.BytesIO(payload))


def _paragraphs(document):
    """每一段：文本、缩进（毫米）、悬挂（毫米）、行内图宽（毫米）。"""
    w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    rows = []
    for paragraph in document.element.body.findall(w_ns + "p"):
        ind = paragraph.find(w_ns + "pPr/" + w_ns + "ind")
        left = ind.get(w_ns + "left") if ind is not None else None
        hanging = ind.get(w_ns + "hanging") if ind is not None else None
        text = "".join(node.text or "" for node in paragraph.iter(w_ns + "t"))
        extent = paragraph.find(".//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent")
        width = round(int(extent.get("cx")) / 36000.0, 2) if extent is not None else None
        rows.append(
            {
                "text": text,
                "left_mm": round(int(left) * 25.4 / 1440, 2) if left else 0.0,
                "hanging_mm": round(int(hanging) * 25.4 / 1440, 2) if hanging else 0.0,
                "image_width_mm": width,
            }
        )
    return rows


class AnswerBlockIndentTests(SimpleTestCase):
    def test_the_answer_paragraph_is_indented_into_the_answer_column(self):
        rows = _paragraphs(_export(_captured()))
        answer = [row for row in rows if "答案：" in row["text"]]
        self.assertEqual(len(answer), 1, rows)
        self.assertAlmostEqual(answer[0]["left_mm"], EXPECTED_INDENT_MM, delta=0.02)

    def test_the_question_number_stays_flush_left(self):
        rows = _paragraphs(_export(_captured()))
        answer = [row for row in rows if "答案：" in row["text"]][0]
        # 悬挂缩进：首行顶格放题号，制表符之后才是正文。
        self.assertAlmostEqual(answer["hanging_mm"], EXPECTED_INDENT_MM, delta=0.02)

    def test_the_analysis_paragraph_is_indented_too(self):
        rows = _paragraphs(_export(_captured()))
        analysis = [row for row in rows if "解析：" in row["text"]]
        self.assertEqual(len(analysis), 1, rows)
        self.assertAlmostEqual(analysis[0]["left_mm"], EXPECTED_INDENT_MM, delta=0.02)
        self.assertAlmostEqual(analysis[0]["hanging_mm"], 0.0, delta=0.02)

    def test_the_solution_figure_is_indented_and_fits_the_column(self):
        rows = _paragraphs(_export(_captured()))
        pictures = [row for row in rows if row["image_width_mm"] is not None]
        self.assertEqual(len(pictures), 1, rows)
        self.assertAlmostEqual(pictures[0]["left_mm"], EXPECTED_INDENT_MM, delta=0.02)
        self.assertLessEqual(pictures[0]["image_width_mm"], EXPECTED_COLUMN_MM + 0.02)

    def test_the_indent_follows_the_font_size(self):
        # 12 / 14 / 16 磅：题号列是 2.4em，缩进跟着字号走。
        for size, expected in ((12, 11.22), (14, 12.92), (16, 14.61)):
            with self.subTest(size=size):
                options = library_drafts._print_options(
                    {"document": "answers", "student_info": False, "font_size": size},
                    {"answer_layout": "appendix"},
                    ids=[UID],
                )
                rows = _paragraphs(_export(_captured(), options))
                answer = [row for row in rows if "答案：" in row["text"]][0]
                self.assertAlmostEqual(answer["left_mm"], expected, delta=0.02)

    def test_the_word_constants_match_the_stylesheet_grid(self):
        css = CSS_PATH.read_text(encoding="utf-8")
        match = re.search(r"\.print-answer-row\s*\{([^}]*)\}", css)
        self.assertIsNotNone(match, "样式表里找不到 .print-answer-row")
        rule = match.group(1)
        self.assertIn("2.4em", rule, rule)
        self.assertRegex(rule, r"gap:\s*4px")
        self.assertAlmostEqual(
            export.PREVIEW_ANSWER_NUMBER_COLUMN_EM, 2.4, places=3, msg="Word 侧的题号列宽跟样式表对不上"
        )
        self.assertEqual(export.PREVIEW_ANSWER_COLUMN_GAP_PX, 4)
        self.assertAlmostEqual(
            export._answer_block_indent_mm(12), EXPECTED_INDENT_MM, delta=0.01, msg="Word 侧的缩进跟样式表对不上"
        )

    def test_an_inline_answer_is_indented_without_a_question_number(self):
        options = library_drafts._print_options(
            {"document": "combined", "answers": True, "student_info": False}, {"answer_layout": "inline"}, ids=[UID]
        )
        rows = _paragraphs(_export(_captured(), options))
        answer = [row for row in rows if "答案：" in row["text"]][0]
        self.assertAlmostEqual(answer["left_mm"], EXPECTED_INDENT_MM, delta=0.02)
        self.assertAlmostEqual(answer["hanging_mm"], 0.0, delta=0.02)

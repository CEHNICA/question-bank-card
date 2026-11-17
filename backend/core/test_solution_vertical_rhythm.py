"""答案块的竖向节奏和结构：Word 必须跟打印预览一模一样。

预览（frontend/library.css + frontend/library-solutions.js）里一个答案块长这样：

    div.print-answer-row           margin: 6px 0（上下各 4.5 磅）
      div.solution-result          无外边距，「答案：」跟正文同行
      strong.solution-label        「解析：」独占一行（直接挂在容器上）
      div.qb-analysis.solution-paragraph   margin: 3pt 0

Word 当年把「解析：」写进正文段的前缀，纸上就挤成了一行；行距又用「倍数行距」
1.35 倍 —— 那不是 CSS 的 1.35em，是宋体自然行高（1.29em）的 1.35 倍，于是每行
比屏幕上高 0.39em（实测 1.67 毫米），几十行下来整版都松一截。

这里锁五件事（判据一律写独立字面值，不引用被测模块的常量 —— 否则反向变体
把常量一改，这条守护会跟着一起放绿）：

1. 「解析：」独占一段，正文在另一段；
2. 每段的行距是「最小值 16.2 磅」（12 磅字 × 1.35），不是「精确值」也不是倍数；
3. 答案段前后距、解析正文段前后距，照抄预览的外边距；
4. 两个答案块之间的空白 = 预览里两个 4.5 磅的外边距；
5. 表格单元格行高 1.6em、段后距清零。

``w:spacing`` 的 before/after 单位是 1/20 磅，line 单位也是 1/20 磅。
"""

import io

from django.test import SimpleTestCase

from . import library_drafts, library_export as export

UID1 = "11111111-2222-3333-4444-555555555555"
UID2 = "11111111-2222-3333-4444-666666666666"
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# 预览侧的字面值（frontend/library.css）：
#   .print-answer-row { margin: 6px 0 }          → 上下各 4.5 磅
#   .solution-paragraph { margin: 3pt 0 }         → 上下各 3 磅
#   .print-flow .print-answer-row { line-height: 1.35 }
ANSWER_ROW_MARGIN_PT = 4.5
SOLUTION_PARAGRAPH_MARGIN_PT = 3.0
LINE_HEIGHT_EM = 1.35
BODY_SIZE_PT = 12.0
EXPECTED_LINE_PT = BODY_SIZE_PT * LINE_HEIGHT_EM  # 16.2
EXPECTED_TABLE_LINE_PT = BODY_SIZE_PT * 1.6  # 19.2
EXPECTED_SECTION_LINE_PT = 18.0
EXPECTED_TITLE_LINE_PT = 27.0


def _item(uid, where, answer="答案是 42。", analysis="解析如下。"):
    return {
        "id": uid,
        "where": where,
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
        "solution_images": [],
        "solution_id": None,
        "selection_revision": None,
        "solution_explicit": False,
    }


def _captured():
    return [_item(UID1, "第 1 题"), _item(UID2, "第 2 题", answer="答案是 7。", analysis="")]


def _export(captured=None, options=None):
    captured = captured if captured is not None else _captured()
    options = options or library_drafts._print_options(
        {"document": "answers", "student_info": False}, {"answer_layout": "appendix"}, ids=[UID1, UID2]
    )
    payload = export._document(captured, "答案块竖向节奏", options, options["document"])
    from docx import Document

    return Document(io.BytesIO(payload))


def _spacing(paragraph_element):
    """w:spacing 的 before/after/line（磅）/lineRule。"""
    node = paragraph_element.find(W_NS + "pPr/" + W_NS + "spacing")
    if node is None:
        return {"before": None, "after": None, "line": None, "rule": None}
    return {
        "before": None if node.get(W_NS + "before") is None else int(node.get(W_NS + "before")) / 20.0,
        "after": None if node.get(W_NS + "after") is None else int(node.get(W_NS + "after")) / 20.0,
        "line": None if node.get(W_NS + "line") is None else int(node.get(W_NS + "line")) / 20.0,
        "rule": node.get(W_NS + "lineRule"),
    }


def _rows(document):
    """文档正文里的每一段：文本 + 间距属性。"""
    out = []
    for element in document.element.body.findall(W_NS + "p"):
        text = "".join(node.text or "" for node in element.iter(W_NS + "t"))
        out.append({"text": text, "spacing": _spacing(element)})
    return out


def _find(rows, needle):
    hits = [row for row in rows if needle in row["text"]]
    if len(hits) != 1:
        raise AssertionError(f"「{needle}」应该只出现一次，实际 {len(hits)} 次：{[r['text'] for r in rows]}")
    return hits[0]


class SolutionVerticalRhythmTests(SimpleTestCase):
    def test_the_analysis_label_occupies_a_paragraph_of_its_own(self):
        rows = _rows(_export())
        labels = [row for row in rows if row["text"].strip() == "解析："]
        self.assertEqual(len(labels), 1, [row["text"] for row in rows])

    def test_the_analysis_body_is_not_glued_to_the_label(self):
        rows = _rows(_export())
        body = _find(rows, "解析如下。")
        self.assertNotIn("解析：", body["text"])
        self.assertNotEqual(body["text"].strip(), "解析：")

    def test_every_answer_block_paragraph_uses_at_least_line_spacing(self):
        rows = _rows(_export())
        # 「参考答案与解析」是答案卷的小标题，行距另有 18 磅，不算答案块段落。
        block = [
            row
            for row in rows
            if ("答案：" in row["text"] or "解析" in row["text"]) and row["text"] != "参考答案与解析"
        ]
        self.assertTrue(block)
        for row in block:
            with self.subTest(text=row["text"]):
                self.assertEqual(row["spacing"]["rule"], "atLeast")
                self.assertAlmostEqual(row["spacing"]["line"], EXPECTED_LINE_PT, delta=0.05)

    def test_the_answer_paragraph_keeps_the_preview_margins(self):
        rows = _rows(_export())
        first = _find(rows, "1.")
        self.assertAlmostEqual(first["spacing"]["before"], ANSWER_ROW_MARGIN_PT, delta=0.05)
        self.assertAlmostEqual(first["spacing"]["after"], 0.0, delta=0.05)

    def test_the_analysis_body_uses_the_preview_margins(self):
        rows = _rows(_export())
        body = _find(rows, "解析如下。")
        self.assertAlmostEqual(body["spacing"]["before"], SOLUTION_PARAGRAPH_MARGIN_PT, delta=0.05)

    def test_the_gap_between_two_answer_blocks_matches_the_preview(self):
        rows = _rows(_export())
        body = _find(rows, "解析如下。")
        second = _find(rows, "2.")
        # 预览：解析正文自己的 3 磅 + 上一块的下外边距 4.5 磅 + 下一块的上外边距 4.5 磅
        expected = SOLUTION_PARAGRAPH_MARGIN_PT + ANSWER_ROW_MARGIN_PT * 2
        self.assertAlmostEqual(body["spacing"]["after"], expected, delta=0.05)
        self.assertAlmostEqual(second["spacing"]["before"], 0.0, delta=0.05)

    def test_the_section_heading_uses_its_own_line_height(self):
        rows = _rows(_export())
        heading = _find(rows, "参考答案与解析")
        self.assertAlmostEqual(heading["spacing"]["line"], EXPECTED_SECTION_LINE_PT, delta=0.05)
        self.assertEqual(heading["spacing"]["rule"], "atLeast")

    def test_the_document_title_uses_its_own_line_height(self):
        rows = _rows(_export())
        title = _find(rows, "答案块竖向节奏")
        self.assertAlmostEqual(title["spacing"]["line"], EXPECTED_TITLE_LINE_PT, delta=0.05)
        self.assertEqual(title["spacing"]["rule"], "atLeast")

    def test_table_cells_use_the_preview_line_height(self):
        from docx import Document

        document = Document()
        rows = [
            {"header": True, "rowspan": 1, "colspan": 1, "segments": [{"type": "text", "raw": "表头"}]},
            {"header": False, "rowspan": 1, "colspan": 1, "segments": [{"type": "text", "raw": "数据"}]},
        ]
        export._write_table(document, [[rows[0]], [rows[1]]], BODY_SIZE_PT, "第 1 题")
        cells = document.element.body.findall(".//" + W_NS + "tc")
        self.assertTrue(cells)
        for cell in cells:
            paragraph = cell.find(W_NS + "p")
            self.assertIsNotNone(paragraph)
            spacing = _spacing(paragraph)
            self.assertEqual(spacing["rule"], "atLeast")
            self.assertAlmostEqual(spacing["line"], EXPECTED_TABLE_LINE_PT, delta=0.05)
            self.assertAlmostEqual(spacing["after"], 0.0, delta=0.05)

    def test_the_line_spacing_helper_never_uses_exact_spacing(self):
        """「精确值」会把大分数和行内图裁掉 —— 只能用「最小值」。"""
        from docx import Document

        document = Document()
        paragraph = document.add_paragraph("测试")
        export._line_spacing(paragraph, EXPECTED_LINE_PT)
        spacing = _spacing(paragraph._element)
        self.assertEqual(spacing["rule"], "atLeast")
        self.assertAlmostEqual(spacing["line"], EXPECTED_LINE_PT, delta=0.05)

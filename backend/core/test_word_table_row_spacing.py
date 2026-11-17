"""导出 Word：表格里的段落继承段后距，把每一行都撑高。

病根（真 Word 实测 _exp116.py，四个变体对照）：

    Word 表格的行高 = 单元格内段落行高 **+ 该段的段后距**。
    选项表格（_write_compact_options）自己建表，没给单元格段落写段后距，
    于是全都继承 Normal 样式的 3 磅 —— 每一行白胖 3 磅，
    整张表比预览高 3 磅，后面的内容全被顶下去。

    实测（选项表①末行 → 下一题题干这一处边界）：
        现状              Word 比预览宽 1.062 毫米
        表格内段后距清零   Word 比预览宽 0.003 毫米   ← 病根坐实

    另一处边界（选项表②末行 → 下一个大题标题）：
        现状 1.641 毫米 → 清零后 0.583 毫米。

预览侧的真值：

    .qb-options { row-gap: .1em }   → 行与行之间 0.1em（12 磅字号 = 1.2 磅）
    .qb-option-body { white-space: pre-wrap } → 一个选项就是**一个块**，
                                               块内没有段后距这回事
    Word 的表格行之间没有间距，行间距只能靠段落段后距顶出来。

所以：

    单栏（选项直接写在正文里）：每段段后距 = 0.1em（预览的 row-gap）
    多栏（选项在表格里）：段后距 = 0；多行时**非末行**给 0.1em 顶出 row-gap，
                          末行给 0（表格底不能多出来）

判据一律写独立字面值，不引用被测模块的常量 —— 否则反向变体把常量一改，
这条守护会跟着一起放绿。
"""

import io
import os
import re
from pathlib import Path

from django.test import SimpleTestCase

from . import library_drafts, library_export as export

REPO = Path(__file__).resolve().parents[2]
FRONTEND = Path(os.environ.get("QB_FRONTEND_ROOT") or (REPO / "frontend"))
CSS = FRONTEND / "styles.css"

CHOICE = "99999999-9999-4999-8999-999999999999"
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# 预览 .qb-options { row-gap: .1em }：12 磅字号 → 0.1 × 12 = 1.2 磅 = 24 twips
ROW_GAP_TWIPS = 24
# Normal 样式默认的段后距：3 磅 = 60 twips（就是它把每一行撑高的）
NORMAL_AFTER_TWIPS = 60


def _text(raw):
    return [{"type": "text", "segments": [{"type": "text", "raw": raw}]}]


def _item(uid, where, kind, stem, answer="", analysis="", options=None):
    fields = {"stem": _text(stem), "answer": _text(answer), "analysis": _text(analysis)}
    for letter, value in (options or {}).items():
        fields[f"options.{letter}"] = _text(value)
    return {
        "id": uid,
        "where": where,
        "type": kind,
        "content": {"stem": stem, "options": options or {}, "origin": "", "body_mode": "text"},
        "extras": {},
        "selected": {"answer": answer, "analysis": analysis},
        "ai": False,
        "hash": "",
        "fields": fields,
        "images": [],
        "solution_images": [],
        "solution_id": None,
        "selection_revision": None,
        "solution_explicit": False,
    }


def _captured():
    return [
        _item(
            CHOICE,
            "第 1 题",
            "single_choice",
            "下列各式中，正确的是（　　）",
            "B",
            "由定义可知。",
            {"A": "x > 1", "B": "x < 1", "C": "x = 1", "D": "x ≥ 1"},
        )
    ]


def _options(columns):
    options = library_drafts._print_options(
        {"document": "questions", "question_breaks": []},
        {"answer_layout": "appendix", "answer_space": "none", "student_info": True},
        ids=[CHOICE],
    )
    options["option_columns"] = {CHOICE: columns}
    return options


def _document(columns):
    payload = export._document(_captured(), "表格行高对照卷", _options(columns), "questions")
    from docx import Document

    return Document(io.BytesIO(payload))


def _twips(paragraph):
    """段后距：先看段落自己写的，没写就看样式上的（Word 就是这么继承的）。"""
    fmt = paragraph.paragraph_format
    if fmt.space_after is not None:
        return int(fmt.space_after.twips)
    style = paragraph.style
    style_fmt = style.paragraph_format if style is not None else None
    if style_fmt is not None and style_fmt.space_after is not None:
        return int(style_fmt.space_after.twips)
    return None


def _option_tables(document):
    """正文里的表格（选项表格就是其中之一）。"""
    return list(document.tables)


def _row_after_twips(document, table_index, row_index):
    """某一行里所有**直接**单元格的段落后距（嵌套表里的段落不算）。"""
    table = _option_tables(document)[table_index]
    row = table.rows[row_index]
    values = []
    for cell in row.cells:
        for paragraph in cell.paragraphs:
            values.append(_twips(paragraph))
    return values


class PreviewOptionGapFactsTests(SimpleTestCase):
    """尺子自检：判据引用的预览事实本身要站得住。"""

    def test_the_preview_option_rows_have_a_tenth_of_an_em_gap(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.qb-options\s*\{[^}]*row-gap:\s*\.1em")

    def test_the_preview_option_body_is_one_pre_wrapped_block(self):
        """一个选项就是一块（pre-wrap），块内根本没有段后距这回事。"""
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.qb-option-body\s*\{[^}]*white-space:\s*pre-wrap")

    def test_the_normal_style_still_carries_three_points_of_space_after(self):
        """病根：Normal 样式有 3 磅段后距，表格里的段落会继承它。"""
        document = _document(4)
        normal = document.styles["Normal"].paragraph_format
        self.assertIsNotNone(normal.space_after)
        self.assertEqual(int(normal.space_after.twips), NORMAL_AFTER_TWIPS)


class OptionTableRowSpacingTests(SimpleTestCase):
    """多栏：选项在表格里，单元格段落的段后距必须显式写死。"""

    def test_a_single_row_of_options_has_no_space_after(self):
        """四个选项排成一行：预览里没有行间距，Word 也不能有段后距。"""
        document = _document(4)
        values = _row_after_twips(document, 0, 0)
        self.assertTrue(values)
        self.assertEqual(set(values), {0}, values)

    def test_the_first_row_of_a_two_row_table_carries_the_row_gap(self):
        """四个选项排两行：预览行距 0.1em，靠段后距顶出来。"""
        document = _document(2)
        values = _row_after_twips(document, 0, 0)
        self.assertTrue(values)
        self.assertEqual(set(values), {ROW_GAP_TWIPS}, values)

    def test_the_last_row_of_a_two_row_table_has_no_space_after(self):
        """末行不能有段后距：表格底要多出来，后面全被顶下去。"""
        document = _document(2)
        values = _row_after_twips(document, 0, 1)
        self.assertTrue(values)
        self.assertEqual(set(values), {0}, values)

    def test_no_option_cell_inherits_the_normal_space_after(self):
        """回归：任何一个选项格子都不能还挂着 Normal 的 3 磅。"""
        for columns in (1, 2, 4):
            document = _document(columns)
            for table in _option_tables(document):
                for row in table.rows:
                    for cell in row.cells:
                        for paragraph in cell.paragraphs:
                            self.assertNotEqual(_twips(paragraph), NORMAL_AFTER_TWIPS)


class SingleColumnOptionSpacingTests(SimpleTestCase):
    """单栏：选项直接写在正文里，段后距也要写成预览的 0.1em。"""

    def test_the_option_paragraphs_carry_the_row_gap(self):
        document = _document(1)
        rows = []
        for paragraph in document.paragraphs:
            text = paragraph.text
            if re.match(r"^[A-D]\.", text.strip()):
                rows.append((text, _twips(paragraph)))
        self.assertEqual(len(rows), 4, [p.text for p in document.paragraphs])
        for text, after in rows[:-1]:
            self.assertEqual(after, ROW_GAP_TWIPS, text)

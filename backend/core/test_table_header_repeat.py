"""第四十四轮：表格跨页时，后一页要把列名再印一遍。

第四十四轮实测（真 PDF，Chrome printToPDF 出的纸）：

    修之前
      40 行 4 列的表跨 2 页
      第 1 页有列名（序号 / 项目名称 / 单位 / 备注说明）
      第 2 页一个列名都没有 —— 只剩一串数字，读者根本不知道那一列是什么

    修之后
      第 2 页顶上又来一遍列名

根因：exam-layout.js 里补表头的那段找的是 <thead>，可渲染出来的表格
压根没有 thead（首行只是 th）—— 整段等于死代码。

两条路线各修各的，但必须是一个效果：

    PDF 路线（浏览器排版）：qb-render.js 给表头行打 qb-table-head-row，
        exam-layout.js 续页时把这行克隆一份放到片段表格最上面。
    Word 路线：首行写 w:tblHeader，Word 自己在每页顶端重复它。

跑法：Windows 上设 QB_EXPORT_BROWSER_TEST=1（CI 的 backend/manage.py test core
就带着这个环境变量），真 PDF 那两条才会跑。
"""

import os
import unittest
from unittest import skipUnless

from django.test import SimpleTestCase
from docx import Document
from docx.oxml.ns import qn

from core import library_export

HEADERS = ["序号", "项目名称", "单位", "备注说明"]
BODY_ROWS = 40


def cell(text, header=False):
    return {"source": text, "header": header, "colspan": 1, "rowspan": 1, "segments": [{"type": "text", "raw": text}]}


def word_rows(with_header=True):
    head = [cell(name, header=with_header) for name in HEADERS]
    rows = [head]
    for index in range(1, BODY_ROWS + 1):
        rows.append([cell(str(index)), cell(f"第{index}号实验项目"), cell("千克"), cell(f"第{index}行的补充说明")])
    return rows


def trpr_tags(table, row_index=0):
    trpr = table.rows[row_index]._tr.find(qn("w:trPr"))
    if trpr is None:
        return []
    return [child.tag.split("}")[-1] for child in trpr]


def markdown_table():
    lines = ["| " + " | ".join(HEADERS) + " |", "|---|---|---|---|"]
    for index in range(1, BODY_ROWS + 1):
        lines.append(f"| {index} | 第{index}号实验项目 | 千克 | 第{index}行的补充说明 |")
    return "\n".join(lines)


def captured():
    return [
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "type": "free_response",
            "content": {"stem": markdown_table(), "options": {}},
            "selected": {},
            "ai": False,
            "solution_images": [],
            "images": [],
        }
    ]


class WordHeaderRowTests(SimpleTestCase):
    """Word 路线：首行得标成 w:tblHeader，Word 才会跨页重复它。"""

    def test_the_first_row_is_marked_as_a_repeating_header(self):
        document = Document()
        library_export._write_table(document, word_rows(), 12, "守护")
        tags = trpr_tags(document.tables[0])
        self.assertIn("tblHeader", tags, f"首行没有 tblHeader（{tags}）：Word 不会在续页重复表头")
        self.assertIn("cantSplit", tags, "每行都要能整行搬页")

    def test_a_table_without_a_header_row_is_not_marked(self):
        """首行不是表头的表，别乱标 —— 重复出来是错的。"""
        document = Document()
        library_export._write_table(document, word_rows(with_header=False), 12, "守护")
        self.assertNotIn("tblHeader", trpr_tags(document.tables[0]))


@skipUnless(
    os.name == "nt" and os.environ.get("QB_EXPORT_BROWSER_TEST") == "1",
    "Windows: set QB_EXPORT_BROWSER_TEST=1 for the real browser PDF check",
)
class PdfHeaderRepeatTests(SimpleTestCase):
    """PDF 路线：真渲染出来的纸，续页顶上必须有列名。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import pymupdf

        from core import library_pdf as pdf

        document = pdf._html_document(captured(), "表头重复体检", pdf._print_options({"student_info": False}))
        cls.data, cls.pages = pdf._render(document)
        cls.texts = []
        with pymupdf.open(stream=cls.data, filetype="pdf") as result:
            for page in result:
                cls.texts.append(page.get_text())

    def test_the_table_really_spans_two_pages(self):
        """尺子自检：得真的排到跨页，不然下面那条不算数。"""
        self.assertGreaterEqual(len(self.texts), 2, f"只排了 {len(self.texts)} 页，尺子没被考验到")
        self.assertIn("第1号实验项目", self.texts[0], "第一页连第一行数据都没有")
        self.assertIn("第40号实验项目", self.texts[-1], "最后一页连最后一行数据都没有")

    def test_the_next_page_repeats_the_column_names(self):
        for name in HEADERS:
            self.assertIn(name, self.texts[0], f"第一页连列名「{name}」都没有")
            self.assertIn(name, self.texts[1], f"第二页没有重复列名「{name}」：读者根本不知道那一列是什么")


if __name__ == "__main__":
    unittest.main()

"""页面设置：正文净宽必须等于预览那端的正文宽。

第四十二轮的教训：体检器自己拿 python-docx 默认模板造文档，那份模板是
Letter 纸加 1 英寸边距，正文净宽只有 165 毫米 —— 178 毫米的表在里头当然
「出血」。那是尺子的锅，不是实现的锅。

现在页面设置抽成了 ``_page_setup``，生产（``_document``）和体检器共用同一份。
这个文件把这份设置锁住：改了一边忘了另一边，尺子的前提就废了。

判据：
  1. 纸张是 A4（210 × 297 毫米），左右边距 16 毫米，上下 18 毫米
  2. 正文净宽 = 210 - 16 * 2 = 178 毫米，和 PREVIEW_TEXT_WIDTH_MM 一个数
  3. ``_document`` 必须走 ``_page_setup``，不能自己另起一套
"""

import inspect

from django.test import SimpleTestCase
from docx import Document

from . import library_export


class PageSetupTests(SimpleTestCase):
    def test_the_paper_is_a4(self):
        document = Document()
        library_export._page_setup(document)
        section = document.sections[0]
        self.assertAlmostEqual(section.page_width / 36000, 210.0, places=1)
        self.assertAlmostEqual(section.page_height / 36000, 297.0, places=1)

    def test_the_margins_are_the_ones_the_tables_assume(self):
        document = Document()
        library_export._page_setup(document)
        section = document.sections[0]
        self.assertAlmostEqual(section.left_margin / 36000, 16.0, places=1)
        self.assertAlmostEqual(section.right_margin / 36000, 16.0, places=1)
        self.assertAlmostEqual(section.top_margin / 36000, 18.0, places=1)
        self.assertAlmostEqual(section.bottom_margin / 36000, 18.0, places=1)

    def test_the_text_width_is_the_one_the_preview_uses(self):
        """表格列宽按 PREVIEW_TEXT_WIDTH_MM 分配，正文净宽必须正好是它。"""
        document = Document()
        library_export._page_setup(document)
        section = document.sections[0]
        text_width = (section.page_width - section.left_margin - section.right_margin) / 36000
        self.assertAlmostEqual(text_width, library_export.PREVIEW_TEXT_WIDTH_MM, places=1)

    def test_the_document_builder_uses_the_shared_setup(self):
        """_document 必须调 _page_setup，不能自己再写一套纸张边距。"""
        source = inspect.getsource(library_export._document)
        self.assertIn("_page_setup(document)", source)
        self.assertNotIn("section.page_width", source)

"""导出 Word：汉字旁边的空格要跟预览一样不留。

预览（frontend/qb-render.js 的 tidyText）把「一周计划生产 700 辆自行车」印成
「一周计划生产700辆自行车」；Word 原来照抄原文，纸上多出一截空格 —— 真 Word
实测同一行预览 96.0 磅、Word 100.91 磅，其中 2.98 磅就是那个空格。真题库里有
1000 多处这样的空格（题干 673、解析 333、题源 3）。

断言锁的是「纸上印出来的字」：从导出的 docx 里把文字取出来看，不是看某个
函数的返回值。预览那侧的规矩用 frontend/qb-render.js 的源码钉住，防止两边
各改各的。
"""

import io
import unittest
import zipfile
from pathlib import Path

from django.test import TestCase

from . import library_export as export
from .test_library_export import ExportFixture, document_xml

PREVIEW = Path(__file__).resolve().parents[2] / "frontend" / "qb-render.js"
STYLES = Path(__file__).resolve().parents[2] / "frontend" / "styles.css"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class WordSpacingTests(unittest.TestCase):
    """排版规则本身。"""

    def test_a_space_between_a_chinese_word_and_a_number_is_dropped(self):
        self.assertEqual(export._word_text("一周计划生产 700 辆自行车"), "一周计划生产700辆自行车")

    def test_a_space_between_two_latin_words_is_kept(self):
        self.assertEqual(export._word_text("x = 1"), "x = 1")

    def test_a_space_next_to_a_formula_is_kept(self):
        # 公式旁边那个空格是缝：预览靠 .qb-math { margin: 0 .12em }，Word 靠空格。
        self.assertEqual(export._word_text("已知 "), "已知 ")
        self.assertEqual(export._word_text(" 答案"), " 答案")

    def test_runs_of_spaces_collapse_like_the_preview(self):
        self.assertEqual(export._word_text("求  x  的值"), "求x的值")

    def test_the_cjk_range_is_the_one_the_preview_uses(self):
        for char in ("纸", "。", "（", "　", "答"):
            self.assertTrue(export._is_cjk(char), f"{char!r} 应当算汉字")
        for char in ("A", "1", " ", "-", "x"):
            self.assertFalse(export._is_cjk(char), f"{char!r} 不该算汉字")


class WordAutoSpacingTests(ExportFixture, TestCase):
    """Word 的「自动调整中文与西文的间距」得关掉。

    Word 默认在汉字和西文/数字之间塞 1/4 汉字宽（12 磅时实测 2.98 磅）的
    空隙，预览那边没有这条规矩。真 Word 实测同一行「答案：答案是42。」：
    预览 96.0 磅；开着自动间距 103.91 磅（是→4 之间 2.98 磅）；关掉之后
    100.91 磅，那条缝没了 —— 剩下的 1.9 磅是宋体的字形边距，不是布局差。

    只删原文里的空格是改不动行宽的：Word 里显式空格会让位给自动间距，
    「答案是 42。」和「答案是42。」在 Word 里一样宽。

    这两个属性在 pPr 里有固定位置，位置错了 Word 直接忽略。
    """

    def _docx(self, **options):
        payload = self.payload([self.publication(stem="看图作答。", answer="答案是 42。")], print_options=options)
        result = self.post(payload)
        self.assertEqual(result.status_code, 200, result.content[:300])
        return result.content

    def _paragraphs(self, xml_bytes):
        from lxml import etree

        tree = etree.fromstring(xml_bytes)
        return list(tree.iter(W + "p"))

    def test_every_paragraph_switches_the_auto_spacing_off(self):
        tree = document_xml(self._docx(answers=False))
        paragraphs = list(tree.iter(W + "p"))
        self.assertTrue(paragraphs, "导出的文档里一个段落都没有")
        for paragraph in paragraphs:
            properties = paragraph.find(W + "pPr")
            self.assertIsNotNone(properties, "有个段落没有 pPr")
            for tag in ("autoSpaceDE", "autoSpaceDN"):
                node = properties.find(W + tag)
                self.assertIsNotNone(node, f"有段落没关 {tag}")
                self.assertEqual(node.get(W + "val"), "0", f"{tag} 不是关着的")

    def test_the_switch_sits_where_word_looks_for_it(self):
        # Word 只认固定位置：autoSpaceDE/DN 要排在 w:spacing / w:ind / w:jc /
        # w:rPr 之前。位置放错就直接被忽略（曾经就栽在这儿）。
        tree = document_xml(self._docx(answers=False))
        later = {"spacing", "ind", "jc", "rPr", "sectPr"}
        for paragraph in tree.iter(W + "p"):
            properties = paragraph.find(W + "pPr")
            order = [child.tag.replace(W, "") for child in properties]
            for tag in ("autoSpaceDE", "autoSpaceDN"):
                self.assertIn(tag, order, f"段落里少了 {tag}")
                position = order.index(tag)
                for sibling in later:
                    if sibling in order:
                        self.assertLess(position, order.index(sibling), f"{tag} 排在 {sibling} 后面，Word 不认这个位置")

    def test_the_footer_switches_it_off_too(self):
        # 页脚里也有汉字和数字挨着（「第 1 页 共 2 页」）。
        with zipfile.ZipFile(io.BytesIO(self._docx(answers=False))) as archive:
            names = [name for name in archive.namelist() if name.startswith("word/footer")]
            self.assertTrue(names, "导出的文档没有页脚")
            for name in names:
                for paragraph in self._paragraphs(archive.read(name)):
                    properties = paragraph.find(W + "pPr")
                    self.assertIsNotNone(properties, f"{name} 里有段落没有 pPr")
                    for tag in ("autoSpaceDE", "autoSpaceDN"):
                        node = properties.find(W + tag)
                        self.assertIsNotNone(node, f"{name} 里有段落没关 {tag}")
                        self.assertEqual(node.get(W + "val"), "0")


class PreviewSpacingBaselineTests(unittest.TestCase):
    """预览那侧的规矩：汉字旁的空格在屏幕上就已经没了。"""

    def setUp(self):
        self.source = PREVIEW.read_text(encoding="utf-8")

    def test_the_preview_drops_spaces_next_to_chinese_characters(self):
        self.assertRegex(
            self.source,
            r"\(\?=\[\\u3000-\\u303f\\uff00-\\uffef\\u4e00-\\u9fff\]\)"
            r"\|\(\?<=\[\\u3000-\\u303f\\uff00-\\uffef\\u4e00-\\u9fff\]\)",
            "预览必须还在按「汉字旁的空格删掉」这条规矩排",
        )

    def test_the_preview_uses_the_same_chinese_range_as_the_export(self):
        for code in ("\\u3000-\\u303f", "\\uff00-\\uffef", "\\u4e00-\\u9fff"):
            self.assertIn(code, self.source, f"预览的汉字范围缺了 {code}")

    def test_the_preview_does_not_add_cjk_latin_spacing(self):
        # 浏览器那边的「自动中西文间距」是 CSS Text Level 4 的 text-spacing。
        # 预览里没有这条规矩，Word 有 —— 所以 Word 那边得显式关掉，
        # 两边才对得上。预览这边要是哪天加上了，这条得改。
        self.assertNotIn("text-spacing", STYLES.read_text(encoding="utf-8"))


class WordDocumentSpacingTests(ExportFixture, TestCase):
    """从导出的 docx 里把字取出来看：纸上印的到底是哪几个字。"""

    def _paper(self, pubs, **options):
        payload = self.payload(pubs, print_options=options)
        result = self.post(payload)
        self.assertEqual(result.status_code, 200, result.content[:300])
        return "".join(document_xml(result.content).itertext())

    def test_the_stem_on_paper_has_no_space_next_to_a_chinese_word(self):
        pub = self.publication(stem="一周计划生产 700 辆自行车")
        text = self._paper([pub], answers=False)
        self.assertIn("一周计划生产700辆自行车", text)
        self.assertNotIn("生产 700", text)

    def test_the_answer_on_paper_has_no_space_next_to_a_chinese_word(self):
        pub = self.publication(stem="看图作答。", answer="答案是 42。")
        text = self._paper([pub], document="answers")
        self.assertIn("答案是42。", text)
        self.assertNotIn("是 42", text)

    def test_latin_spaces_stay_on_paper(self):
        pub = self.publication(stem="求 x = 1 的值")
        text = self._paper([pub], answers=False)
        self.assertIn("x = 1", text)

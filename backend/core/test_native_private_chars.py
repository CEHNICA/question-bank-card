# -*- coding: utf-8 -*-
"""一页里有三个字体私有区字符，整页的题不该因此从题库里消失。

真卷验收（《凤城高一数学试题_打印版_v8.pdf》，2025 级高一质量检测一）：
第 2 页文字层 945 个字符里 942 个都正常，只有 U+F8F1/F8F2/F8F3 三个私有区
码位（分段函数的大括号、取整号）。native_pdf 原来判它是「乱码」，整页降级去
手工框题，于是**第 9–15 题七道题一道都没切出来** —— 切题器本身切出了 19 题，
是入库前那道「不跨扫描页」的过滤把它们丢掉的，警告还误写成「有无法读取的页面」。

私有区不是乱码：那是 PDF 字体厂商给数学符号的正常编码，在原图上印出来是对的。
判乱码只该看替换字符 U+FFFD，而且零星几个不该把整页的题判死刑。

这里直接测判据本身，不造 PDF：PyMuPDF 的默认字体没有 U+F8F2 的字形，
写进测试 PDF 时那个码位就被丢掉了（造出来的是一页干净页，测不出任何东西）。
"""

import pathlib
import tempfile

import pymupdf
from django.test import SimpleTestCase

from . import native_pdf


_PDF_DIRS = []


def _write_pdf(body):
    """造一份带文字层的 PDF。PyMuPDF 默认字体没有私有区码位的字形，
    所以这里只用来验 extract 的接线，私有区那几条直接测判据函数。"""
    directory = tempfile.mkdtemp()
    _PDF_DIRS.append(directory)
    path = pathlib.Path(directory) / "fixture.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(pymupdf.Rect(60, 60, 540, 760), body, fontsize=11)
    document.save(path)
    document.close()
    return path


PRIVATE = ""          # 分段函数的大括号在这类 PDF 字体里的真实编码
REPLACEMENT = "�"
BODY = "已知函数 f(x) 的最小值问题。" * 40


class ExtractWiringTests(SimpleTestCase):
    """判据抽出来以后，extract 还得真的调得到它。

    page_mode 的第二个参数是仅关键字的。写成位置调用会抛 TypeError，而那行在
    逐页的 try 里，一抛就被 except 吞掉 —— 每页都变成「文字层提取失败」，一张
    卡都切不出来，判据本身的单元测试却全绿。所以这里真的跑一遍 extract。
    """

    def test_extract_returns_native_pages_for_an_ordinary_text_pdf(self):
        result = native_pdf.extract(_write_pdf(BODY))
        page = result["pages"][0]
        self.assertEqual(page["mode"], "native")
        self.assertGreater(len(result["blocks"]), 0, "A page with text must yield blocks")
        self.assertFalse(any("提取失败" in w for w in page["warnings"]),
                         f"extract must reach page_mode instead of failing into the per-page handler: {page['warnings']}")

    def test_extract_reports_the_private_encoding_note(self):
        result = native_pdf.extract(_write_pdf(BODY))
        self.assertTrue(any("不能保证分式" in w for w in result["pages"][0]["warnings"]),
                        f"Every native page carries that note, got {result['pages'][0]['warnings']}")


class PageModeTests(SimpleTestCase):
    """什么情况下这一页才该交给老师手工框题。"""

    def test_private_characters_alone_do_not_condemn_a_page(self):
        self.assertEqual(native_pdf.page_mode(f"{PRIVATE} 12. 已知函数 f(x) = {PRIVATE} ，求 f(f(−2)) ．"),
                         "native",
                         "A page whose only odd characters are font-private must still cut normally")

    def test_a_page_of_replacement_characters_is_still_manual(self):
        self.assertEqual(native_pdf.page_mode(REPLACEMENT * 400 + "���"), "manual",
                         "A body that genuinely will not decode must not be trusted")

    def test_a_handful_of_replacements_does_not_condemn_the_page(self):
        self.assertEqual(native_pdf.page_mode(BODY + "���" + BODY), "native",
                         "Three missing characters in a long page must not cost the page its questions")

    def test_a_whole_scan_dominated_page_is_manual(self):
        self.assertEqual(native_pdf.page_mode(BODY, full_page_image=True), "manual",
                         "A page that is mostly a scan still needs the teacher")

    def test_an_empty_page_is_manual(self):
        self.assertEqual(native_pdf.page_mode("  \n "), "manual")


class PageWarningTests(SimpleTestCase):

    def test_the_note_says_private_encoding_not_corruption(self):
        warnings = native_pdf.classify_page_text(f"{PRIVATE} 12. 已知函数 f(x) = {PRIVATE}")
        self.assertTrue(any("私有编码" in w for w in warnings), f"Expected a private-encoding note, got {warnings}")
        self.assertFalse(any("乱码" in w for w in warnings),
                         "Font-private symbols are not corruption; saying so sends the teacher hunting a bad file")

    def test_lost_characters_are_still_counted(self):
        warnings = native_pdf.classify_page_text(BODY + "���")
        self.assertTrue(any("3 个字符读不出来" in w for w in warnings), f"got {warnings}")

    def test_an_empty_page_says_it_has_no_text(self):
        self.assertIn("没有可复制文字，保留原页并手工框题。", native_pdf.classify_page_text("   "))


class PrivatePlaceholderTests(SimpleTestCase):

    def test_private_characters_become_a_visible_placeholder(self):
        marked = native_pdf.mark_private(f"{PRIVATE} 12. 已知函数 f(x) = {PRIVATE}")
        self.assertNotIn(PRIVATE, marked, "A raw private-use code point must not reach the draft text")
        self.assertEqual(marked.count("□"), 4, "Each uncopyable symbol becomes one visible placeholder")

    def test_ordinary_text_is_untouched(self):
        self.assertEqual(native_pdf.mark_private(BODY), BODY)
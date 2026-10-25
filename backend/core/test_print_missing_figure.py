"""配图文件不在了，导出必须拒绝，不能印一张缺图的卷子出来。

隔离库纸面验收（tmp/isomiss）：把第 2 题的 figure-1.png 挪走再导一次，
PDF 和 Word 两条路都停在同一句
「第 131 题（数学九年级上册.pdf，原卷第 2 题）· 配图 1：文件缺失、损坏或过大，
请检查这道题的入库配图」，一个字节的卷子都没产出。PDF 路线和 Word 路线共用
library_export._capture，所以两边行为一致。

这道题库里的图哪天会不在：清过 library 目录、换机器只搬了库、
备份只备了 db.sqlite3。真库当前 959 张卡里 0 张缺图（tmp/probe_missing_figures.py），
所以这是守行为、不是修缺陷 —— 哪天有人改成「静默跳过这张图」，这里会红。

同一件事的另一面在 frontend/qb-render.js：题库界面上 figureElement 会给缺图的题
印「点『查看出处』可以回到原卷核对」。那是给老师看的操作指引，**导出路径走不到**
（_capture 先拦住了），所以不用改文案；真走到了才会把它印给学生对。
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

from . import library_export, library_pdf


class ExportRefusesMissingFiguresTests(SimpleTestCase):
    def test_a_deleted_figure_file_is_caught_as_oserror(self):
        source = Path(library_export.__file__).read_text(encoding="utf-8")
        block = re.search(r"def _images\(.*?\n    return images\n", source, re.S)
        self.assertIsNotNone(block, "library_export.py must keep _images()")
        self.assertRegex(
            block.group(0),
            r"except \(OSError",
            "A deleted figure raises FileNotFoundError, which is an OSError. Without it in "
            "the except clause the whole export dies with a traceback instead of a message.",
        )

    def test_the_refusal_names_the_question_and_the_source_paper(self):
        source = Path(library_export.__file__).read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r"where = f\"第 \{position\} 题（\{publication\.source_filename",
            "「本卷第 N 题」不够用：老师要回原卷核对，得知道是哪份原卷的第几题",
        )
        self.assertIn("请检查这道题的入库配图", source)

    def test_both_routes_share_that_capture_instead_of_differing(self):
        """PDF 路线复用 Word 那套 _capture；一旦有人给 PDF 单独开一条路，这条会红。

        也因此 library_pdf 的图只会是 _capture 读出来的内联 base64，qb-render.js 里
        figure.missing 那个分支在导出时根本走不到 —— 那句「点『查看出处』」只在题库
        界面上出现，不会印到学生卷子上。
        """
        source = Path(library_pdf.__file__).read_text(encoding="utf-8")
        self.assertIn("from . import library_export as word", source)
        self.assertIn("word._capture(", source, "The PDF route must go through the same capture")
        export_source = Path(library_export.__file__).read_text(encoding="utf-8")
        self.assertIn(
            "images = _images(publication, where) if question_fields else []",
            export_source,
            "Every question that prints figures has to go through _images",
        )

"""选项表格里的图，不许压到隔壁列上。

Word 不会自动缩图。一张比正文列宽的图画在单元格里，Word 就让它压出去 ——
实测（Word 16）：4 栏选项里放进一张 169.33 毫米的图，图从 16 毫米一路画到
185.33 毫米，把后面几列的内容全盖掉了。

以前这条只有一条很窄的路是安全的：前端把预览量到的图宽传过来（实测值）。
没有实测值时（老前端、预览没量到、别的调用方），后端照 150dpi 自然尺寸排，
宽图直接压列。

现在收口落在后端：选项表里每张图最多占满自己那一格的**正文列**宽 ——
也就是预览里正文那一列的宽（栏宽减去标签和它后面的间隙）。
实测值照旧优先（预览量到多少就是多少），只是不再允许它超过正文列。

这里的判据用**独立字面值**（Chrome 2026-11-17 实测的预览几何），不引用
被测模块的常量。
"""

import io

from django.test import SimpleTestCase
from docx.oxml.ns import qn

QUESTION = "11111111-1111-4111-8111-111111111111"

TEXT_WIDTH_MM = 178.0
LIST_INDENT_MM = 8.4667  # 2em
COLUMN_GAP_MM = 8.4667  # 2em
# 正文列的左缘离栏左缘多远（= 标签宽 + 0.3em）：「A.」比「B.」宽。
LABEL_OFFSET_MM = {"A": 5.386, "B": 5.151, "C": 5.151, "D": 5.386}


def png_bytes(width=1000, height=200):
    """A real PNG: the export writes the bytes straight into the document."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def item_with_option_image(slot="A", width=1000, height=200):
    return {
        "id": QUESTION,
        "images": [{"slot": slot, "bytes": png_bytes(width, height), "size": (width, height)}],
        "fields": {
            "options.A": [{"type": "text", "raw": "A", "segments": []}],
            "options.B": [{"type": "text", "raw": "B", "segments": []}],
            "options.C": [{"type": "text", "raw": "C", "segments": []}],
            "options.D": [{"type": "text", "raw": "D", "segments": []}],
        },
    }


def build_options(columns=4, measured=None, item=None):
    """The options table of one question, as the export writes it."""
    from docx import Document

    from . import library_export

    document = Document()
    figures = {"measured": measured} if measured is not None else {}
    library_export._write_compact_options(
        document, item or item_with_option_image(), 12, columns, text_width_mm=TEXT_WIDTH_MM, figures=figures
    )
    return document


def option_cell_images(document):
    """Every picture inside the options table, with the cell it sits in."""
    found = []
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for drawing in cell._tc.iter(qn("w:drawing")):
                    extent = next(drawing.iter(qn("wp:extent")), None)
                    if extent is not None:
                        found.append((cell, int(extent.get("cx")) / 36000.0))
    return found


def body_column_mm(columns, letter="A", text_width_mm=TEXT_WIDTH_MM):
    """预览里这个选项的正文列有多宽（毫米）。

    栏宽 = (正文宽 − 2em − 栏间 2em) ÷ 栏数；正文列 = 栏宽 − 标签缩进。
    """
    column_mm = (text_width_mm - LIST_INDENT_MM - (columns - 1) * COLUMN_GAP_MM) / columns
    return column_mm - LABEL_OFFSET_MM[letter]


def body_images(document):
    """Every picture in the document body (not inside an options table)."""
    found = []
    for drawing in document.element.body.iter(qn("w:drawing")):
        extent = next(drawing.iter(qn("wp:extent")), None)
        if extent is not None:
            found.append(int(extent.get("cx")) / 36000.0)
    return found


class OptionCellFigureWidthTests(SimpleTestCase):
    def test_a_wide_picture_without_a_measurement_stays_inside_its_cell(self):
        # 1000 像素按 150dpi 自然尺寸 = 169.33 毫米，远超 4 栏 A 的正文列 30.65。
        document = build_options(columns=4, measured=None)
        pictures = option_cell_images(document)
        self.assertTrue(pictures, "尺子自检：选项表里没找到图")
        usable = body_column_mm(4, "A")
        for _, width_mm in pictures:
            self.assertLessEqual(
                width_mm,
                usable + 0.05,
                f"选项里的图画成了 {width_mm:.2f} 毫米，正文列只有 {usable:.2f} 毫米：压到隔壁列上了",
            )

    def test_the_measurement_is_still_honoured(self):
        # 预览量到 20 毫米就排 20 毫米，这条收口不许把它改小。
        document = build_options(columns=4, measured={"A": [20.0]})
        widths = [width for _, width in option_cell_images(document)]
        self.assertTrue(widths, "尺子自检：选项表里没找到图")
        self.assertAlmostEqual(widths[0], 20.0, places=1)

    def test_a_measurement_wider_than_the_cell_is_cut_to_the_cell(self):
        # 越界的实测值（预览量错了、或者换了更宽的版式）按正文列收口。
        document = build_options(columns=2, measured={"A": [100.0]})
        widths = [width for _, width in option_cell_images(document)]
        usable = body_column_mm(2, "A")
        self.assertTrue(widths, "尺子自检：选项表里没找到图")
        self.assertLessEqual(widths[0], usable + 0.05)
        self.assertAlmostEqual(widths[0], usable, places=1)

    def test_two_pictures_in_one_option_stay_inside_the_cell_too(self):
        item = item_with_option_image(slot="A")
        item["images"].append({"slot": "A", "bytes": png_bytes(600, 120), "size": (600, 120)})
        document = build_options(columns=4, measured=None, item=item)
        usable = body_column_mm(4, "A")
        pictures = option_cell_images(document)
        self.assertTrue(pictures, "尺子自检：选项表里没找到图")
        for _, width_mm in pictures:
            self.assertLessEqual(
                width_mm, usable + 0.05, f"并排的一张图画成了 {width_mm:.2f} 毫米，正文列只有 {usable:.2f} 毫米"
            )

    def test_the_option_cell_keeps_no_side_padding(self):
        # 预览里选项那一栏两侧不留白，Word 默认每格留 1.905 毫米 ——
        # 不清掉的话正文列会比预览窄 3.81 毫米，折行位置就不对。
        document = build_options(columns=4)
        table = document.tables[0]
        for row in table.rows:
            for cell in row.cells:
                properties = cell._tc.find(qn("w:tcPr"))
                self.assertIsNotNone(properties, "选项格没有 w:tcPr")
                margins = properties.find(qn("w:tcMar"))
                self.assertIsNotNone(margins, "选项格没写 w:tcMar，Word 会留 1.905 毫米的默认内边距")
                for side in ("top", "left", "bottom", "right"):
                    self.assertEqual(
                        int(margins.find(qn("w:" + side)).get(qn("w:w"))), 0, f"选项格的 {side} 内边距不该留白"
                    )

    def test_a_picture_in_the_body_is_not_cut_to_a_cell_width(self):
        # 题干配图不在格子里，能占满正文宽：这条收口不许误伤它。
        from docx import Document

        from . import library_export

        document = Document()
        item = item_with_option_image(slot="stem")
        library_export._write_images(document, item, "stem")
        widths = body_images(document)
        self.assertTrue(widths, "尺子自检：正文里没找到图")
        self.assertAlmostEqual(widths[0], 169.33, places=1)

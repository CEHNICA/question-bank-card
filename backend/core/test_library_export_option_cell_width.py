"""选项表格里的图，不许压到隔壁列上。

Word 不会自动缩图。一张比格子宽的图画在单元格里，Word 就让它压出去 ——
实测（Word 16）：4 栏选项（列宽 44.5 毫米、净宽 40.69 毫米）里放进一张
169.33 毫米的图，图从 16 毫米一路画到 185.33 毫米，把后面几列的内容全盖掉了。

以前这条只有一条很窄的路是安全的：前端把预览量到的图宽传过来（实测值）。
没有实测值时（老前端、预览没量到、别的调用方），后端照 150dpi 自然尺寸排，
宽图直接压列。

现在收口落在后端：选项表里每张图最多占满自己那一格的净宽。
实测值照旧优先（预览量到多少就是多少），只是不再允许它超过格子。
"""

import io

from django.test import SimpleTestCase

from . import library_export

QUESTION = "11111111-1111-4111-8111-111111111111"


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

    document = Document()
    figures = {"measured": measured} if measured is not None else {}
    library_export._write_compact_options(document, item or item_with_option_image(), 12, columns, figures=figures)
    return document


def option_cell_images(document):
    """Every picture inside the options table, with the cell it sits in."""
    from docx.oxml.ns import qn

    found = []
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for drawing in cell._tc.iter(qn("w:drawing")):
                    extent = next(drawing.iter(qn("wp:extent")), None)
                    if extent is not None:
                        found.append((cell, int(extent.get("cx")) / 36000.0))
    return found


def usable_width_mm(document):
    """How wide a picture may be inside one option cell."""
    table = document.tables[0]
    column_mm = table.columns[0].width / 36000.0
    return column_mm - 2 * library_export.WORD_CELL_MARGIN_MM


def body_images(document):
    """Every picture in the document body (not inside an options table)."""
    from docx.oxml.ns import qn

    found = []
    for drawing in document.element.body.iter(qn("w:drawing")):
        extent = next(drawing.iter(qn("wp:extent")), None)
        if extent is not None:
            found.append(int(extent.get("cx")) / 36000.0)
    return found


class OptionCellFigureWidthTests(SimpleTestCase):
    def test_a_wide_picture_without_a_measurement_stays_inside_its_cell(self):
        # 1000 像素按 150dpi 自然尺寸 = 169.33 毫米，远超 4 栏的净宽 40.69。
        document = build_options(columns=4, measured=None)
        pictures = option_cell_images(document)
        self.assertTrue(pictures, "尺子自检：选项表里没找到图")
        usable = usable_width_mm(document)
        for _, width_mm in pictures:
            self.assertLessEqual(
                width_mm,
                usable + 0.5,
                f"选项里的图画成了 {width_mm:.2f} 毫米，格子只有 {usable:.2f} 毫米：压到隔壁列上了",
            )

    def test_the_measurement_is_still_honoured(self):
        # 预览量到 30.88 毫米就排 30.88 毫米，这条收口不许把它改小。
        document = build_options(columns=4, measured={"A": [30.88]})
        widths = [width for _, width in option_cell_images(document)]
        self.assertTrue(widths, "尺子自检：选项表里没找到图")
        self.assertAlmostEqual(widths[0], 30.88, places=1)

    def test_a_measurement_wider_than_the_cell_is_cut_to_the_cell(self):
        # 越界的实测值（预览量错了、或者换了更宽的版式）按格子净宽收口。
        document = build_options(columns=4, measured={"A": [60.0]})
        widths = [width for _, width in option_cell_images(document)]
        usable = usable_width_mm(document)
        self.assertTrue(widths, "尺子自检：选项表里没找到图")
        self.assertLessEqual(widths[0], usable + 0.5)
        self.assertAlmostEqual(widths[0], usable, places=1)

    def test_two_pictures_in_one_option_stay_inside_the_cell_too(self):
        item = item_with_option_image(slot="A")
        item["images"].append({"slot": "A", "bytes": png_bytes(600, 120), "size": (600, 120)})
        document = build_options(columns=4, measured=None, item=item)
        usable = usable_width_mm(document)
        pictures = option_cell_images(document)
        self.assertTrue(pictures, "尺子自检：选项表里没找到图")
        for _, width_mm in pictures:
            self.assertLessEqual(
                width_mm, usable + 0.5, f"并排的一张图画成了 {width_mm:.2f} 毫米，格子只有 {usable:.2f} 毫米"
            )

    def test_the_cell_margin_is_words_default(self):
        # Word 默认左右各 108 twips = 1.905 毫米；Word 16 实测 44.5 毫米的列
        # 净宽 40.69 毫米。写错这个数，收口就会松 0.1 毫米或者过紧。
        self.assertAlmostEqual(library_export.WORD_CELL_MARGIN_MM, 1.9, places=2)
        self.assertAlmostEqual(library_export._option_cell_usable_mm(4, 178), 44.5 - 3.8, places=2)

    def test_a_picture_in_the_body_is_not_cut_to_a_cell_width(self):
        # 题干配图不在格子里，能占满正文宽：这条收口不许误伤它。
        from docx import Document

        document = Document()
        item = item_with_option_image(slot="stem")
        library_export._write_images(document, item, "stem")
        widths = body_images(document)
        self.assertTrue(widths, "尺子自检：正文里没找到图")
        self.assertAlmostEqual(widths[0], 169.33, places=1)

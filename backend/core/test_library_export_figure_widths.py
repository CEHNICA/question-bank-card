"""预览里图有多宽，导出的 Word 里就得有多宽。

选项里的图在屏幕上会被选项栏宽收口：一张 205 像素宽的图放进窄栏里，浏览器
按 ``max-width: 100%`` 把它缩到栏宽（实测 116.72 像素 = 30.88 毫米）。Word
那边不知道有这回事，照 150dpi 自然尺寸排 —— 34.7 毫米。于是屏幕上核对过的那
张图，拿到手大了 3.8 毫米。

预览量出来的实际宽度现在随导出请求一起过去，Word 照它排；没有实测值时
（老前端、PDF 路径）Word 仍按自然尺寸排 —— 那条路本来就跟预览一致，不能被
这条修复带歪。
"""

import io
import zipfile

from django.test import SimpleTestCase

from . import library_export
from .library_drafts import DraftError, _print_options

QUESTION = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"

PNG = None


def png_bytes(width=205, height=81):
    """A real PNG: the export writes the bytes straight into the document."""
    global PNG
    if PNG is None:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
        PNG = buffer.getvalue()
    return PNG


def picture_width_mm(document):
    """The width of the first picture in the document, in millimetres."""
    from docx.oxml.ns import qn

    body = document.element.body
    for drawing in body.iter(qn("w:drawing")):
        extent = next(drawing.iter(qn("wp:extent")), None)
        if extent is not None:
            return int(extent.get("cx")) / 36000.0
    return None


class MeasuredFigureWidthTests(SimpleTestCase):
    def test_the_measured_width_wins_over_the_natural_size(self):
        pictures = [{"slot": "C", "bytes": png_bytes(), "size": (205, 81)}]
        natural = library_export._figure_widths_for_slot(pictures, "C")
        # 205 像素按 150dpi 自然尺寸 = 34.73 毫米。
        self.assertAlmostEqual(natural[0], 34.73, places=1)
        # 预览量出 30.88 毫米（被选项栏宽收口过），Word 照这个排。
        measured = library_export._figure_widths_for_slot(pictures, "C", measured=[30.88])
        self.assertAlmostEqual(measured[0], 30.88, places=2)

    def test_a_measurement_wider_than_the_picture_never_widens_it(self):
        pictures = [{"slot": "stem", "bytes": png_bytes(), "size": (205, 81)}]
        widths = library_export._figure_widths_for_slot(pictures, "stem", measured=[120.0])
        self.assertAlmostEqual(widths[0], 34.73, places=1)

    def test_the_measurement_is_matched_to_its_own_position(self):
        from docx import Document

        # 题干和选项各自量各自的位置：选项 C 的图不能被题干的实测宽度带偏，
        # 题干的实测宽度也不能把选项里的图压小。
        document = Document()
        item = {
            "id": QUESTION,
            "images": [{"slot": "C", "bytes": png_bytes(), "size": (205, 81)}],
            "fields": {"options.C": [{"type": "text", "raw": "C", "segments": []}]},
        }
        library_export._write_compact_options(
            document, item, 12, 4, figures={"measured": {"C": [30.88], "stem": [5.0]}}
        )
        self.assertAlmostEqual(picture_width_mm(document), 30.88, places=1)

    def test_a_measurement_only_covers_the_figures_it_names(self):
        pictures = [
            {"slot": "stem", "bytes": png_bytes(), "size": (205, 81)},
            {"slot": "stem", "bytes": png_bytes(), "size": (205, 81)},
        ]
        widths = library_export._figure_widths_for_slot(pictures, "stem", measured=[20.0])
        self.assertAlmostEqual(widths[0], 20.0, places=2)
        self.assertAlmostEqual(widths[1], 34.73, places=1)

    def test_the_body_slot_is_never_measured(self):
        pictures = [{"slot": "body", "bytes": png_bytes(), "size": (205, 81)}]
        widths = library_export._figure_widths_for_slot(pictures, "body", measured=[5.0])
        self.assertGreater(widths[0], 5.0)


class WrittenPictureWidthTests(SimpleTestCase):
    def test_the_picture_in_the_word_file_is_the_measured_width(self):
        from docx import Document

        document = Document()
        item = {"id": QUESTION, "images": [{"slot": "C", "bytes": png_bytes(), "size": (205, 81)}], "fields": {}}
        library_export._write_images(document, item, "C", measured=[30.88])
        self.assertAlmostEqual(picture_width_mm(document), 30.88, places=1)

    def test_without_a_measurement_the_picture_keeps_its_natural_size(self):
        from docx import Document

        document = Document()
        item = {"id": QUESTION, "images": [{"slot": "C", "bytes": png_bytes(), "size": (205, 81)}], "fields": {}}
        library_export._write_images(document, item, "C")
        self.assertAlmostEqual(picture_width_mm(document), 34.73, places=1)

    def test_the_height_follows_the_measured_width(self):
        from docx import Document
        from docx.oxml.ns import qn

        document = Document()
        item = {"id": QUESTION, "images": [{"slot": "C", "bytes": png_bytes(), "size": (205, 81)}], "fields": {}}
        library_export._write_images(document, item, "C", measured=[30.88])
        extent = next(document.element.body.iter(qn("wp:extent")))
        width = int(extent.get("cx")) / 36000.0
        height = int(extent.get("cy")) / 36000.0
        # 205×81 的图缩到 30.88 毫米宽，高度按比例走。
        self.assertAlmostEqual(height, 30.88 * 81 / 205, places=1)
        self.assertGreater(width, 0)


class FigureSettingsTests(SimpleTestCase):
    def test_the_settings_carry_the_measured_widths_of_that_question(self):
        options = {"measured_figure_widths": {QUESTION: {"C": [30.88]}, OTHER: {"stem": [12.0]}}}
        settings = library_export._figure_settings(options, {"id": QUESTION})
        self.assertEqual(settings["measured"], {"C": [30.88]})

    def test_a_question_without_measurements_gets_an_empty_map(self):
        settings = library_export._figure_settings({}, {"id": QUESTION})
        self.assertEqual(settings["measured"], {})

    def test_broken_measurements_are_dropped_not_obeyed(self):
        options = {"measured_figure_widths": {QUESTION: {"C": ["30", -1, None, 30.5]}}}
        settings = library_export._figure_settings(options, {"id": QUESTION})
        self.assertEqual(settings["measured"], {"C": [30.5]})


class PrintOptionsMeasuredWidthTests(SimpleTestCase):
    def test_the_measured_widths_survive_normalization(self):
        result = _print_options({"measured_figure_widths": {QUESTION: {"C": [30.88]}}}, ids=[QUESTION])
        self.assertEqual(result["measured_figure_widths"], {QUESTION: {"C": [30.88]}})

    def test_no_measured_widths_is_still_valid(self):
        result = _print_options({}, ids=[QUESTION])
        self.assertEqual(result["measured_figure_widths"], {})

    def test_an_unknown_position_is_refused(self):
        for slots in ({"body": [10.0]}, {"X": [10.0]}, {"": [10.0]}):
            with self.assertRaises(DraftError):
                _print_options({"measured_figure_widths": {QUESTION: slots}}, ids=[QUESTION])

    def test_broken_widths_are_refused(self):
        for values in ([0], [-3], [200], ["30"], [None], [True], "30", 30, [[30]]):
            with self.assertRaises(DraftError):
                _print_options({"measured_figure_widths": {QUESTION: {"C": values}}}, ids=[QUESTION])

    def test_a_malformed_question_id_is_refused(self):
        with self.assertRaises(DraftError):
            _print_options({"measured_figure_widths": {"not-a-uuid": {"C": [30.0]}}}, ids=[QUESTION])

    def test_widths_for_questions_outside_the_paper_are_refused(self):
        with self.assertRaises(DraftError):
            _print_options({"measured_figure_widths": {OTHER: {"C": [30.0]}}}, ids=[QUESTION])

    def test_too_many_measurements_are_refused(self):
        many = {f"{index:08d}-1111-4111-8111-111111111111": {"C": [10.0]} for index in range(501)}
        with self.assertRaises(DraftError):
            _print_options({"measured_figure_widths": many}, ids=[QUESTION])


class WrittenDocumentTests(SimpleTestCase):
    def test_the_word_file_carries_the_picture(self):
        from docx import Document

        document = Document()
        item = {"id": QUESTION, "images": [{"slot": "C", "bytes": png_bytes(), "size": (205, 81)}], "fields": {}}
        library_export._write_images(document, item, "C", measured=[30.88])
        buffer = io.BytesIO()
        document.save(buffer)
        with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
            self.assertTrue(any(name.startswith("word/media/") for name in archive.namelist()))

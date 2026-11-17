"""解析配图导出到 Word 后的纸面尺寸，必须和打印预览一致。

预览侧（``exam-layout.js`` 的 ``imageScale``）对所有图片一视同仁：宽度不超过
正文净宽 178mm，高度不超过一页版心 210mm。解析配图在预览里由
``library-solutions.js`` 先写成 ``style.width = display_width mm``，再由
exam-layout 统一收口。

Word 侧 ``_write_solution_image`` 当年自己留了一条 235mm 的高度上限，于是高瘦
的解析图在屏幕上是 96.9mm、在 Word 里印成 101.6mm；老师核对的是屏幕，拿到手的
文件却比屏幕上大了一圈。正文截图那条 235mm 早已被修掉（``test_source_image_width``），
解析配图是同一个坑的漏网之鱼。

这里把解析配图锁回和预览同一个口径：``min(display_width, 178, 210 * W / H)``。
"""

import io

from django.test import SimpleTestCase

from . import library_drafts, library_export as export

UID = "11111111-2222-3333-4444-555555555555"

# (像素宽高, 老师手填的显示宽度毫米)
CASES = (((600, 1300), 101.6), ((400, 500), 178.0), ((300, 2001), 50.8), ((2000, 280), 50.8))


def _png(width, height):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def _captured(size, display_width):
    """一道带解析配图的题，形状和 ``library_export._capture`` 产出的保持一致。"""
    return [
        {
            "id": UID,
            "where": "第 1 题",
            "type": "free_response",
            "content": {"stem": "看图作答。", "options": {}, "origin": ""},
            "extras": {},
            "selected": {"answer": "", "analysis": "解析如下。"},
            "ai": False,
            "hash": "",
            "fields": {
                "stem": [{"type": "text", "segments": [{"type": "text", "raw": "看图作答。"}]}],
                "answer": [],
                "analysis": [{"type": "text", "segments": [{"type": "text", "raw": "解析如下。"}]}],
            },
            "images": [],
            "solution_images": [
                {
                    "bytes": _png(*size),
                    "size": list(size),
                    "display_width": display_width,
                    "position": "after",
                    "paragraph": 0,
                    "name": "solution-1.png",
                }
            ],
            "solution_id": None,
            "selection_revision": None,
            "solution_explicit": False,
        }
    ]


def _exported_sizes_mm(size, display_width):
    """导出件里那张解析配图在纸面上的宽高（毫米）。"""
    options = library_drafts._print_options(
        {"document": "answers", "student_info": False}, {"answer_layout": "appendix"}, ids=[UID]
    )
    payload = export._document(_captured(size, display_width), "解析配图尺寸", options, options["document"])
    from docx import Document

    reopened = Document(io.BytesIO(payload))
    sizes = []
    for inline in reopened.element.body.xpath(".//wp:inline"):
        extent = inline.find("{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent")
        if extent is not None:
            sizes.append((int(extent.get("cx")) / 36000.0, int(extent.get("cy")) / 36000.0))
    return sizes


def _preview_box_mm(size, display_width):
    """预览里那张解析配图在纸面上的宽高（毫米）。

    预览是两步：exam-layout.js 的 imageScale 先按 150dpi 自然尺寸等比缩，
    宽高各自取整到 CSS 像素；再由 .solution-figure img 的 max-width:100%
    把宽度收进答案正文那一列 —— 高度不动。

    这里的 178 / 210 / 166.78 是**预览那边写死的字面值**，故意不引用
    ``library_export`` 里的常量：常量正是反向变体要动的地方，判据若跟着它
    一起变，这条测试就会自己把自己放绿（尺子的自指漏洞，2026-11-17 实测踩过）。
    166.78 = 版心 178 减去答案块题号列那一栏（2.4em + 4px 间隙）。
    """
    width, height = size
    px_per_mm = 96 / 25.4
    scale = min(display_width * px_per_mm / width, 178 * px_per_mm / width, 210 * px_per_mm / height)
    box_width_px = round(width * scale)
    box_height_px = round(height * scale)
    box_width_px = min(box_width_px, round(166.78 * px_per_mm))
    return box_width_px * 25.4 / 96, box_height_px * 25.4 / 96


class SolutionFigureSizeTests(SimpleTestCase):
    def test_the_exported_size_matches_the_preview_rule(self):
        for size, display_width in CASES:
            with self.subTest(size=size, display_width=display_width):
                sizes = _exported_sizes_mm(size, display_width)
                self.assertEqual(len(sizes), 1, "导出件里应当只有那张解析配图")
                expected = _preview_box_mm(size, display_width)
                self.assertAlmostEqual(sizes[0][0], expected[0], delta=0.02)
                self.assertAlmostEqual(sizes[0][1], expected[1], delta=0.02)

    def test_a_tall_solution_figure_stops_at_one_page_body(self):
        sizes = _exported_sizes_mm((300, 2001), 50.8)
        self.assertAlmostEqual(sizes[0][0], 31.48, delta=0.02)
        self.assertAlmostEqual(sizes[0][1], 210.08, delta=0.05)

    def test_a_solution_figure_is_capped_by_the_answer_column_width(self):
        # 老师把显示宽度手填成 178mm，但这张图高宽比 0.8 —— 高度先到顶，
        # 宽度再被答案正文那一栏（净宽 166.78mm）收进去。
        sizes = _exported_sizes_mm((400, 500), 178.0)
        self.assertAlmostEqual(sizes[0][0], 166.69, delta=0.05)
        self.assertAlmostEqual(sizes[0][1], 210.08, delta=0.05)

    def test_no_solution_figure_is_taller_than_the_preview_page_body(self):
        # 判据是预览那边写死的 210mm，不引用被测模块的常量（见 _preview_box_mm 的说明）。
        # 预览实测高度是 210.08 毫米 —— exam-layout 把高度取整到 CSS 像素（794px），
        # 换算回毫米就比 210 多一点点，所以容差留到 210.1。
        for size, display_width in CASES:
            with self.subTest(size=size, display_width=display_width):
                sizes = _exported_sizes_mm(size, display_width)
                self.assertLessEqual(sizes[0][1], 210.1)

    def test_no_solution_figure_is_wider_than_the_answer_column(self):
        # 解析配图在答案正文那一栏里，宽度不能超过那一栏的净宽。
        for size, display_width in CASES:
            with self.subTest(size=size, display_width=display_width):
                sizes = _exported_sizes_mm(size, display_width)
                self.assertLessEqual(sizes[0][0], 166.78)

    def test_a_tall_solution_figure_is_not_stretched_to_the_taller_allowance(self):
        # 235mm 那条遗留上限：预览到 210 就收手，Word 不能再往上放。
        sizes = _exported_sizes_mm((600, 1300), 101.6)
        stale = min(101.6, 235.0 * 600 / 1300)
        self.assertNotAlmostEqual(sizes[0][0], stale, delta=0.02)

"""PDF 路线里的表格，必须留在版心里 —— 而且不能把整份卷子拖下水。

第四十三轮实测（真 PDF，Chrome printToPDF）：

    修之前（格子里 nowrap，宽表不许换行）
      13 列表   右边缘 202.60mm   冲出版心（版心右缘 194mm）
      20 列表   右边缘 209.98mm   冲出纸面（纸宽只有 210mm）
      长文本三列表 右边缘 196.60mm 冲出版心
      整份卷子被 Chrome 缩到 87% 印出来（全卷的字都变小）

    修之后（打印流里格子能换行；13 列以上的表再收紧内边距）
      13 列表 173.04mm / 20 列表 166.16mm / 长文本三列表 173.04mm
      页码那行仍在纸面正中 105.00mm —— 整卷没有被缩放

两个判据都是几何实测，不是源码比对：

  1. 表格墨迹右边缘 ≤ 194mm（= 纸宽 210 - 右边距 16）
  2. 页码那行的水平中心 ≈ 105mm（纸面正中）
     页码是 .exam-page-number 绝对定位在 left:16mm right:16mm 上居中的，
     整卷一旦被缩放，它就会偏到 105mm 左边；被缩到 87% 时它连 280mm 以下
     都不在，压根找不到。

跑法：Windows 上设 QB_EXPORT_BROWSER_TEST=1（CI 的 backend/manage.py test core
就带着这个环境变量）。
"""

import os
import unittest
from unittest import skipUnless

from django.test import SimpleTestCase

from . import library_pdf as pdf

TO_MM = 25.4 / 72
PAPER_MM = 210.0
MARGIN_MM = 16.0
BODY_RIGHT_MM = PAPER_MM - MARGIN_MM
PAGE_CENTER_MM = PAPER_MM / 2
# 页码在纸底（.exam-page-number 的 bottom: 8mm），内容区最底只到 279mm。
PAGE_NUMBER_BELOW_MM = 280.0

TABLES = {
    "three": """| 性质 | 变化情况 | 说明 |
|---|---|---|
| 二次函数 | 开口向上 | 当系数为正时抛物线开口向上 |
""",
    "thirteen": """| 一 | 二 | 三 | 四 | 五 | 六 | 七 | 八 | 九 | 十 | 十一 | 十二 | 十三 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 零售收入 | 增加值 | 增长率 | 零售额 | 比重 | 环比 | 同比 | 累计 | 月度 | 季度 | 年初 | 年末 | 合计 |
""",
    "twenty": """| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 甲 | 乙 | 丙 | 丁 | 戊 | 己 | 庚 | 辛 | 壬 | 癸 | 子 | 丑 | 寅 | 卯 | 辰 | 巳 | 午 | 未 | 申 | 酉 |
""",
    "longtext": """| 序号 | 事件 | 说明 |
|---|---|---|
| 1 | 某工厂进行生产线智能化升级改造 | 升级改造后从甲乙两个车间随机抽取一百五十件产品进行检验 |
""",
}


def captured():
    return [
        {
            "id": f"00000000-0000-0000-0000-0000000000{index:02d}",
            "type": "free_response",
            "content": {"stem": stem, "options": {}},
            "selected": {},
            "ai": False,
            "solution_images": [],
            "images": [],
        }
        for index, (_name, stem) in enumerate(TABLES.items(), 1)
    ]


def table_edges(page):
    """量出这张纸上每张表格的竖线位置（毫米）。"""
    rects = [item["rect"] for item in page.get_drawings()]
    thin = [rect for rect in rects if min(rect.width, rect.height) < 2.0 / TO_MM]
    groups = []
    for rect in [rect for rect in thin if rect.height > rect.width]:
        x = (rect.x0 + rect.x1) / 2 * TO_MM
        y0, y1 = rect.y0 * TO_MM, rect.y1 * TO_MM
        for group in groups:
            if abs(group["x"] - x) <= 0.3 and not (y1 < group["y0"] - 1 or y0 > group["y1"] + 1):
                group["x"] = (group["x"] + x) / 2
                group["y0"] = min(group["y0"], y0)
                group["y1"] = max(group["y1"], y1)
                break
        else:
            groups.append({"x": x, "y0": y0, "y1": y1})
    bands = []
    for group in sorted(groups, key=lambda item: item["x"]):
        for band in bands:
            if not (group["y1"] < band["y0"] - 1 or group["y0"] > band["y1"] + 1):
                band["y0"] = min(band["y0"], group["y0"])
                band["y1"] = max(band["y1"], group["y1"])
                band["x"].append(group["x"])
                break
        else:
            bands.append({"y0": group["y0"], "y1": group["y1"], "x": [group["x"]]})
    tables = []
    for band in bands:
        xs = sorted(band["x"])
        if len(xs) < 2:
            continue
        tables.append({"columns": len(xs) - 1, "left_mm": xs[0], "right_mm": xs[-1]})
    return tables


def page_number_center(page):
    """页码那行文字的水平中心（毫米）；找不到就是 None。"""
    words = [word for word in page.get_text("words") if word[1] * TO_MM > PAGE_NUMBER_BELOW_MM]
    if not words:
        return None
    left = min(word[0] for word in words) * TO_MM
    right = max(word[2] for word in words) * TO_MM
    return (left + right) / 2


@skipUnless(
    os.name == "nt" and os.environ.get("QB_EXPORT_BROWSER_TEST") == "1",
    "Windows: set QB_EXPORT_BROWSER_TEST=1 for the real browser PDF check",
)
class PdfTableWidthTests(SimpleTestCase):
    """表格不许冲出版心，整卷不许被缩放。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import pymupdf

        document = pdf._html_document(captured(), "表格版心体检", pdf._print_options({"student_info": False}))
        cls.data, cls.pages = pdf._render(document)
        cls.measurements = []
        with pymupdf.open(stream=cls.data, filetype="pdf") as result:
            for page in result:
                cls.measurements.append((table_edges(page), page_number_center(page)))

    def test_every_table_stays_inside_the_text_column(self):
        """每张表的右边缘都得在版心右缘（194mm）以内。"""
        tables = [table for page, _ in self.measurements for table in page]
        self.assertGreater(len(tables), 0, "尺子没量到任何表格，这次验收不算数")
        for table in tables:
            self.assertLessEqual(
                table["right_mm"],
                BODY_RIGHT_MM + 0.6,
                f"{table['columns']} 列表的右边缘跑到 {table['right_mm']:.2f}mm，冲出版心了",
            )

    def test_the_paper_is_not_shrunk_to_fit_a_wide_table(self):
        """页码那行必须还在纸面正中（105mm）：偏了就是整卷被缩放了。"""
        centers = [center for _page, center in self.measurements if center is not None]
        self.assertEqual(
            len(centers), len(self.measurements), "有页面在纸底找不到页码：整份卷子被缩放了（缩放后页码会跟着往上跑）"
        )
        for center in centers:
            self.assertAlmostEqual(center, PAGE_CENTER_MM, delta=1.0, msg=f"页码中心在 {center:.2f}mm")

    def test_the_wide_tables_are_actually_on_the_paper(self):
        """尺子自检：这次真的排到了宽表和长文本表。"""
        columns = [table["columns"] for page, _ in self.measurements for table in page]
        self.assertTrue(any(count >= 13 for count in columns), f"没有排到 13 列以上的宽表，尺子没被考验到：{columns}")


if __name__ == "__main__":
    unittest.main()

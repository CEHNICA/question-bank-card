"""Golden Corpus: Regression test suite for real-world maths workbook typography and quirks.

沉淀历史版本（如 1.26.31、1.26.46 等）中踩坑解决的各类教辅真实奇葩排版用例，
防止后续对清洗、切题和正则逻辑重构时造成算法倒退。
"""

from __future__ import annotations

from django.test import SimpleTestCase

from core import textnorm


class GoldenCorpusOriginTests(SimpleTestCase):
    """题源出处提取与剥离回归用例"""

    def test_origin_with_score_and_difficulty(self):
        """1.26.31: 题源中夹带分值、题号、难度时能完整提取，不留在题干中"""
        text = "（2021新高考Ⅰ，7，5分，中）已知函数 f(x) = sin(x) + cos(x)"
        stem, origin = textnorm.split_origin(text)
        self.assertEqual(origin, "2021新高考Ⅰ，7，5分，中")
        self.assertEqual(stem, "已知函数 f(x) = sin(x) + cos(x)")

    def test_origin_behind_category_tag(self):
        """1.26.31: 题源压在栏目名后面时，使用 split_origin_note，栏目名留在题干，题源被摘出"""
        text = "（创新考法）（2024江苏南通一中月考，6）已知数列 {a_n} 为等差数列"
        stem, origin = textnorm.split_origin_note(text)
        self.assertEqual(origin, "2024江苏南通一中月考，6")
        self.assertEqual(stem, "（创新考法）已知数列 {a_n} 为等差数列")

    def test_national_exam_variants(self):
        """全国甲理/全国乙文等卷别简称能正确识别"""
        text = "（2021全国甲理，13，5分，易）设双曲线的离心率为 2"
        stem, origin = textnorm.split_origin(text)
        self.assertEqual(origin, "2021全国甲理，13，5分，易")
        self.assertEqual(stem, "设双曲线的离心率为 2")

    def test_score_notices_are_not_origins(self):
        """普通满分提示（如“本小题满分12分”）不得误当成题源剥离"""
        text = "（本小题满分12分）在平面直角坐标系中，点 A 的坐标为 (1, 2)"
        stem, origin = textnorm.split_origin(text)
        self.assertEqual(origin, "")
        self.assertEqual(stem, "（本小题满分12分）在平面直角坐标系中，点 A 的坐标为 (1, 2)")


class GoldenCorpusLabelStrippingTests(SimpleTestCase):
    """例题及题型标签剥离回归用例"""

    def test_strip_example_label(self):
        """例题前缀清理：只剥除标准例题，不误伤变式"""
        self.assertEqual(textnorm.strip_example_label("【例1】已知函数..."), "已知函数...")
        self.assertEqual(textnorm.strip_example_label("例 2. 求数列..."), "求数列...")
        self.assertEqual(textnorm.strip_example_label("【例题三】设..."), "设...")

    def test_strip_type_label(self):
        """题型中括号前缀清理"""
        stem, qtype = textnorm.strip_type_label("【单选题】下列命题中正确的是")
        self.assertEqual(qtype, "single_choice")
        self.assertEqual(stem, "下列命题中正确的是")


class GoldenCorpusAnchorMismatchTests(SimpleTestCase):
    """锚点行防误报回归用例（1.26.31 经典问题）"""

    def test_origin_in_printed_line_does_not_trigger_mismatch(self):
        """锚点行印着长出处，但出处已被提取进 origin 字段，不得误报卡上是另一道题"""
        printed = "(2025届江苏盐城射阳中学开学考,6) 若实数 x, y 满足约束条件"
        reading = "若实数 x, y 满足约束条件"
        origin = "2025届江苏盐城射阳中学开学考,6"
        mismatch = textnorm.anchor_reading_mismatch(printed, reading, origin=origin)
        self.assertIsNone(mismatch, "不应将已提取出处的题误报为内容不符")

    def test_true_mismatch_is_still_reported(self):
        """若锚点行确实是完全不同的题（连续实词>=6且完全认不出），必须准确报警"""
        printed = "7. 设双曲线满足约束条件且焦点在对称轴上"
        reading = "1. 已知全集 U 为实数集，集合 A 为空集"
        mismatch = textnorm.anchor_reading_mismatch(printed, reading, origin="")
        self.assertIsNotNone(mismatch, "真不符必须被检测到并挂黄条旗标")


class GoldenCorpusOptionCleaningTests(SimpleTestCase):
    """选项前缀清洗回归用例"""

    def test_clean_option_redundant_prefixes(self):
        """自动剥除选项中冗余的 A. / A． / A、 等前缀"""
        self.assertEqual(textnorm.clean_option("A. 3", "A"), "3")
        self.assertEqual(textnorm.clean_option("A．\\frac{1}{2}", "A"), "\\frac{1}{2}")
        self.assertEqual(textnorm.clean_option("A、[0, 1]", "A"), "[0, 1]")
        self.assertEqual(textnorm.clean_option("A: 4", "A"), "4")
        # 选项内容本身就是字母时不得误删
        self.assertEqual(textnorm.clean_option("3", "A"), "3")

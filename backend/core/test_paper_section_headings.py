"""组卷上每个大题前面那一行印什么：屏幕和导出文件必须是同一句话。

老师 2026-10-24 早上 7 点报的问题：「试题篮中的和导出的pdf不一致是怎么回事」。
查下来是这一行：屏幕上的组卷预览印「二、填空题」，导出的 PDF 印原卷那一整行
「二、填空题：本题共 4 小题，每小题 5 分，共 20 分」—— 而那一行里的题数、分值、
题号范围说的全是**原卷**，跟老师自己挑的这几道题对不上。

这一份取值放在 frontend/testdata/paper_section_headings.json，屏幕那一侧
（frontend/exam-export.js 的 sectionHeading）和这一侧
（core/library_export.py 的 _section_heading）都读它 —— 改一边就会红。
"""
import json
import pathlib

from django.test import TestCase

from core import library_export as export

FIXTURE = (pathlib.Path(__file__).resolve().parents[2]
           / "frontend" / "testdata" / "paper_section_headings.json")


class PaperSectionHeadingTests(TestCase):
    def test_the_heading_matches_the_fixture_the_screen_also_reads(self):
        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
        self.assertGreaterEqual(len(cases), 8, "取值至少要覆盖试卷大题说明、教材短章节名和混合的那几种")
        for case in cases:
            group = [{"content": {"section": section}} for section in case["sections"]]
            self.assertEqual(
                export._section_heading(group, case["index"], case["group_name"]),
                case["expected"], case["name"])

    def test_the_original_wording_never_reaches_the_paper(self):
        # 原卷那一整行带着原卷的题数、分值和题号范围，跟老师挑的这几道题对不上，
        # 印出去学生会以为卷子漏了题 —— 屏幕上也不该出现它。
        printed = "一、选择题：本题共 12 小题，每小题 5 分，共 60 分"
        heading = export._section_heading([{"content": {"section": printed}}], 0, "选择题")
        self.assertEqual(heading, "一、选择题")
        for word in ("本题共", "共 60 分", "第 17 题"):
            self.assertNotIn(word, heading)

    def test_the_short_chapter_name_of_a_textbook_is_still_printed(self):
        # 短章节名说的是「这些题出自哪一节」，对新卷子依然成立，不能一起丢。
        printed = "一、选择题：本题共 12 小题，每小题 5 分，共 60 分"
        group = [{"content": {"section": "习题 3.1"}}, {"content": {"section": "习题 3.1"}}]
        self.assertEqual(export._section_heading(group, 0, "选择题"), "习题 3.1")
        # 混着长说明就说不准了，这时用题型名。
        mixed = [{"content": {"section": printed}}, {"content": {"section": "习题 3.1"}}]
        self.assertEqual(export._section_heading(mixed, 0, "选择题"), "一、选择题")

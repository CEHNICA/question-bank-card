"""第三十三轮守护：分卷导出的题目卷必须和单独导出的题目卷一模一样。

尺子实测（_audit33.py）量到四处差异，全在答题留白上：

    分卷导出的题目卷   解答题只剩 8 毫米下限（454 twips）
    单独导出的题目卷   解答题 30 毫米（1701 twips）

同一份题目卷，答题空间取决于「输出内容」那个跟它无关的下拉值，少了 22 毫米。
根因在前端：currentPrintOptions() 把「合卷＋逐题紧跟 ⇒ 留白归零」这条**渲染期**
规则提前烘焙进了 payload。前端那一侧由 frontend/test_library_export_split_space.js
钉死；这份文件钉死后端这一侧：

    分卷的题目卷上根本没有答案紧跟（答案在另一份卷子上），
    所以它不认「逐题紧跟 ⇒ 不留白」那条规则，它的留白只由用户设的值决定。

渲染端各自判自己那份文件：预览（exam-layout.js）、PDF（library_pdf.py）、
Word（_document 的 inline_answers）三边各判一次，规则不再提前烘焙。
"""

from __future__ import annotations

import io
import zipfile

from django.test import TestCase, override_settings

from .test_library_export import NS, ExportFixture, document_xml

TWIPS_PER_MM = 1440 / 25.4


def writing_space(tree, stem):
    """题干后面那块答题留白的高度（twips）；没有留白返回 None。

    第六十九轮起，留白是**固定行高的空段**（w:lineRule="exact"），
    高度写在 w:line 上；w:after 是它后面的题块间距（8 磅），不是留白高度。
    """
    paragraphs = tree.find("w:body", NS).findall("w:p", NS)
    index = next(i for i, p in enumerate(paragraphs) if stem in "".join(p.itertext()))
    for paragraph in paragraphs[index + 1 :]:
        if "".join(paragraph.itertext()).strip():
            break
        spacing = paragraph.find("w:pPr/w:spacing", NS)
        if spacing is not None and spacing.get(f"{{{NS['w']}}}lineRule") == "exact":
            return int(spacing.get(f"{{{NS['w']}}}line"))
    return None


@override_settings(ROOT_URLCONF="core.test_library_export")
class SplitWritingSpaceTests(ExportFixture, TestCase):
    """「分别导出题目卷与答案卷」里的题目卷，跟单独导出的题目卷是同一份。"""

    def split_documents(self, pubs, print_options=None, title="单元练习"):
        response = self.post(self.payload(pubs, print_options=print_options, output_format="split", title=title))
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response["Content-Type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            return {
                "questions": document_xml(archive.read(f"{title}-题目卷.docx")),
                "answers": document_xml(archive.read(f"{title}-答案解析卷.docx")),
            }

    def test_the_split_question_sheet_keeps_the_writing_space_of_a_standalone_question_sheet(self):
        pub = self.publication(kind="free_response", stem="分卷留白对照题干", answer="42")
        standalone = self.post(self.payload([pub], print_options={"document": "questions", "answer_space": "auto"}))
        self.assertEqual(standalone.status_code, 200, standalone.content[:300])
        expected = writing_space(document_xml(standalone.content), "分卷留白对照题干")
        self.assertEqual(expected, round(30 * TWIPS_PER_MM), "单独导出的题目卷给解答题留 30 毫米")

        # 界面上的「输出内容」停在「题目＋答案」「逐题紧跟」，分卷导出的题目卷照样留 30 毫米。
        sheets = self.split_documents(
            [pub], print_options={"document": "combined", "answer_layout": "inline", "answer_space": "auto"}
        )
        self.assertEqual(
            writing_space(sheets["questions"], "分卷留白对照题干"),
            expected,
            "分卷导出的题目卷必须和单独导出的题目卷留一样的答题空间",
        )

    def test_the_split_question_sheet_does_not_depend_on_the_answer_layout_dropdown(self):
        pub = self.publication(kind="free_response", stem="分卷下拉无关题干", answer="42")
        combined = self.split_documents(
            [pub], print_options={"document": "combined", "answer_layout": "inline", "answer_space": "auto"}
        )
        questions = self.split_documents(
            [pub], print_options={"document": "questions", "answer_layout": "inline", "answer_space": "auto"}
        )
        space_combined = writing_space(combined["questions"], "分卷下拉无关题干")
        self.assertIsNotNone(space_combined, "分卷的题目卷上留着答题空间")
        self.assertEqual(
            space_combined,
            writing_space(questions["questions"], "分卷下拉无关题干"),
            "同一份题目卷的答题空间不能取决于一个跟它无关的下拉值",
        )

    def test_the_split_question_sheet_keeps_an_explicit_millimetre_value(self):
        pub = self.publication(kind="free_response", stem="分卷指定毫米题干", answer="42")
        standalone = self.post(self.payload([pub], print_options={"document": "questions", "answer_space": 60}))
        self.assertEqual(standalone.status_code, 200, standalone.content[:300])
        expected = writing_space(document_xml(standalone.content), "分卷指定毫米题干")
        self.assertEqual(expected, round(60 * TWIPS_PER_MM))
        sheets = self.split_documents(
            [pub], print_options={"document": "combined", "answer_layout": "inline", "answer_space": 60}
        )
        self.assertEqual(writing_space(sheets["questions"], "分卷指定毫米题干"), expected)

    def test_the_split_answer_sheet_matches_the_standalone_answer_sheet(self):
        pub = self.publication(kind="free_response", stem="分卷答案卷题干", answer="42", analysis="解析步骤甲乙丙")
        standalone = self.post(self.payload([pub], print_options={"document": "answers", "answer_space": "auto"}))
        self.assertEqual(standalone.status_code, 200, standalone.content[:300])
        sheets = self.split_documents(
            [pub], print_options={"document": "combined", "answer_layout": "inline", "answer_space": "auto"}
        )
        self.assertEqual(
            "".join(sheets["answers"].itertext()),
            "".join(document_xml(standalone.content).itertext()),
            "分卷的答案卷必须和单独导出的答案卷一个字都不差",
        )
        self.assertNotIn("分卷答案卷题干", "".join(sheets["answers"].itertext()), "答案卷上不重复印题干")

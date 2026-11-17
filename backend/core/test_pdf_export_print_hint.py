"""第二十八轮守护：导出的 PDF 里不许出现题库页的「没有可打印的内容」指引。

这轮体检在 PDF 导出链路上抓到一条硬缺陷（不是环境问题，HEAD 上同样复现）：

  library.css 的 @media print 里有
      body:not(.printing)::after { content: "题有据题库页没有可打印的内容。打印试卷请先在「试题篮」里打开组卷预览，再从预览里打印。"; display: block; padding: 40mm 18mm; ... }

  这条指引是给「用户在题库页直接按 Ctrl+P」准备的 —— 题库页本身没有可打印内容，
  与其把目录树、「加入试题篮」全印成十几页废纸，不如给一句指引（设计内行为，不能删）。

  但 PDF 导出走 Chromium 的 Page.printToPDF，它按 print 媒体类型渲染，
  于是这条 @media print 规则照单全收。PDF 文档的 <body> 是裸的（没有 .printing class），
  :not(.printing) 命中 → 这句指引被当成真内容渲染出来，独占一整页。

  实测（4 题合成分页卷）：
      driver 报 3 页，Chromium 实际输出 4 页
      第 4 页文本 = "题有据题库页没有可打印的内容。打印试卷请先在「试题篮」里打开组卷预览，再从预览里打印。"
      → _validate_pdf 判页数对不上 → 抛「PDF 页数或 A4 页面不完整，请重新打开组卷后导出。」
      → PDF 导出整条链路走不通（core.test_pdf_pagination 8 条长红即由此而来）

修法：在 PDF 文档自己的 fixed 样式里精确掐掉这一条伪元素，
     题库页上按 Ctrl+P 时的指引保留不动。

这里锁三件事：
  1) PDF 文档里必须有这条抑制规则（否则修复被删掉也不报警）
  2) library.css 里那句指引必须还在（题库页 Ctrl+P 的行为不能被顺手删掉）
  3) 端到端：真渲染一份 PDF，页数必须自洽，且全文不含那句指引
"""

import re
import uuid

from django.test import SimpleTestCase

from . import library_pdf as pdf

HINT = "没有可打印的内容"

_ITEMS = [
    {
        "type": "single_choice",
        "stem": r"下列各式中，属于最简二次根式的是（ ）",
        "options": {"A": r"$\sqrt{12}$", "B": r"$\sqrt{\frac{3}{2}}$", "C": r"$\sqrt{8}$", "D": r"$\sqrt{0.5}$"},
    },
    {"type": "fill_blank", "stem": r"已知 $f(x)=x^2-2x+3$，则 $f(1)=$____。", "options": {}},
]

_OPTIONS = {
    "document": "questions",
    "answers": False,
    "ai_answers": False,
    "origin": False,
    "font_size": 12,
    "student_info": False,
    "pagination": "compact",
    "option_layout": "auto",
    "answer_space": "small",
    "answer_layout": "appendix",
    "option_overrides": {},
    "answer_space_overrides": {},
    "figure_overrides": {},
    "question_breaks": [],
}


class PdfExportPrintHintTests(SimpleTestCase):
    """PDF 导出不得把题库页的指引印进卷子。"""

    def items(self):
        return [
            {
                "id": str(uuid.UUID(int=index)),
                "type": item["type"],
                "content": {"stem": item["stem"], "options": item["options"]},
                "selected": {},
                "ai": False,
                "solution_images": [],
                "images": [],
            }
            for index, item in enumerate(_ITEMS, 1)
        ]

    # ── 1. 静态：PDF 文档里必须有抑制规则 ──────────────────────────
    def test_pdf_document_suppresses_the_library_page_hint(self):
        document = pdf._html_document(self.items(), "导出体检", dict(_OPTIONS))
        self.assertIn(
            "body:not(.printing)::after",
            document,
            "PDF 文档里找不到抑制规则：题库页那句「没有可打印的内容」"
            "会被 printToPDF 当成真内容印在最后一页，页数校验随即失败，"
            "PDF 导出整条链路走不通",
        )
        match = re.search(r"body:not\(\.printing\)::after\{([^}]*)\}", document)
        self.assertIsNotNone(match, "尺子自检：抑制规则写法变了，这条断言要重写")
        self.assertIn(
            "content:none", match.group(1).replace(" ", ""), "抑制规则里没有 content:none：指引照样会被渲染出来"
        )

    # ── 2. 静态：题库页的指引本身必须还在 ─────────────────────────
    def test_library_page_hint_is_still_there_for_ctrl_p(self):
        from pathlib import Path

        from django.conf import settings

        css = (Path(settings.FRONTEND_ROOT) / "library.css").read_text(encoding="utf-8")
        self.assertIn(
            HINT,
            css,
            "题库页那句指引被删了：用户在题库页直接按 Ctrl+P 时，"
            "整页题库界面（目录树、「加入试题篮」、「看答案▾」）会被原样印出来，"
            "实测十几页全是废纸，一道题都不能用",
        )

    # ── 3. 端到端：真渲染一份 PDF，页数自洽且不含指引 ─────────────
    def test_rendered_pdf_has_no_hint_page(self):
        import pymupdf

        document = pdf._html_document(self.items(), "导出体检", dict(_OPTIONS))
        try:
            data, pages = pdf._render(document)
        except Exception as error:  # noqa: BLE001
            self.fail("PDF 渲染直接失败（页数校验顶爆）：" + str(error))
        with pymupdf.open(stream=data, filetype="pdf") as result:
            self.assertEqual(result.page_count, pages, "页数对不上：排版器算的页数和真正印出来的页数不一致")
            text = "\n".join(page.get_text() for page in result)
        self.assertNotIn(HINT, text, "导出的 PDF 里印着题库页的指引：那一句被当成卷子内容排进了纸里")
        self.assertIn("最简二次根式", text, "尺子自检：PDF 里连题干都没有，渲染路径变了")

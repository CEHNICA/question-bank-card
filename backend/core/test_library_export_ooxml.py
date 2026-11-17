"""导出的 Word（docx）必须是一份合法的 OOXML 文档。

ECMA-376 给 w:pPr / w:tblPr 这些容器规定了固定的子元素顺序。顺序不对的
文件在 Word 里往往还能打开（它会自己重排），但别的排版工具未必，可能把
「整题同页」这类开关连同内容一起丢掉；更糟的是 Word 会「悄悄修复」，
老师拿到的是一份被改过却看不出改过的卷子。

这里量两件事：
1. 导出的 docx 里，这些容器的子元素顺序合不合规（compact 与 keep 两条
   排版路径都要走到，因为两条路径写的开关不一样）。
2. 第三方公式转换库处理不了的公式，只能降级成图片，不许把整份卷子带走。
"""

from __future__ import annotations

import io
import zipfile

from django.test import TestCase
from lxml import etree

from . import library_export as export
from .test_library_export import ExportFixture, field, math_field, units

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

# ECMA-376 的 sequence 约束（只列项目自己会写到的元素）。
ORDER = {
    "pPr": [
        "pStyle",
        "keepNext",
        "keepLines",
        "pageBreakBefore",
        "framePr",
        "widowControl",
        "numPr",
        "suppressLineNumbers",
        "pBdr",
        "shd",
        "tabs",
        "suppressAutoHyphens",
        "kinsoku",
        "wordWrap",
        "overflowPunct",
        "topLinePunct",
        "autoSpaceDE",
        "autoSpaceDN",
        "bidi",
        "adjustRightInd",
        "snapToGrid",
        "spacing",
        "ind",
        "contextualSpacing",
        "mirrorIndents",
        "suppressOverlap",
        "jc",
        "textDirection",
        "textAlignment",
        "textboxTightWrap",
        "outlineLvl",
        "divId",
        "cnfStyle",
        "rPr",
        "sectPr",
        "pPrChange",
    ],
    "rPr": [
        "rStyle",
        "rFonts",
        "b",
        "bCs",
        "i",
        "iCs",
        "caps",
        "smallCaps",
        "strike",
        "dstrike",
        "outline",
        "shadow",
        "emboss",
        "imprint",
        "noProof",
        "snapToGrid",
        "vanish",
        "webHidden",
        "color",
        "spacing",
        "w",
        "kern",
        "position",
        "sz",
        "szCs",
        "highlight",
        "u",
        "effect",
        "bdr",
        "shd",
        "fitText",
        "vertAlign",
        "rtl",
        "cs",
        "em",
        "lang",
        "eastAsianLayout",
        "specVanish",
        "oMath",
        "rPrChange",
    ],
    "tcPr": [
        "cnfStyle",
        "tcW",
        "gridSpan",
        "hMerge",
        "vMerge",
        "tcBorders",
        "shd",
        "noWrap",
        "tcMar",
        "textDirection",
        "tcFitText",
        "vAlign",
        "hideMark",
        "headers",
        "cellIns",
        "cellDel",
        "cellMerge",
        "tcPrChange",
    ],
    "trPr": [
        "cnfStyle",
        "divId",
        "gridBefore",
        "gridAfter",
        "wBefore",
        "wAfter",
        "cantSplit",
        "trHeight",
        "tblHeader",
        "tblCellSpacing",
        "jc",
        "hidden",
        "ins",
        "del",
        "trPrChange",
    ],
    "tblPr": [
        "tblStyle",
        "tblpPr",
        "tblOverlap",
        "bidiVisual",
        "tblStyleRowBandSize",
        "tblStyleColBandSize",
        "tblW",
        "jc",
        "tblCellSpacing",
        "tblInd",
        "tblBorders",
        "shd",
        "tblLayout",
        "tblCellMar",
        "tblLook",
        "tblCaption",
        "tblDescription",
        "tblPrChange",
    ],
    "sectPr": [
        "headerReference",
        "footerReference",
        "footnotePr",
        "endnotePr",
        "type",
        "pgSz",
        "pgMar",
        "paperSrc",
        "pgBorders",
        "lnNumType",
        "pgNumType",
        "cols",
        "formProt",
        "vAlign",
        "noEndnote",
        "titlePg",
        "textDirection",
        "bidi",
        "rtlGutter",
        "docGrid",
        "printerSettings",
        "sectPrChange",
    ],
}


def local_name(element):
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    return tag.rpartition("}")[2]


def order_problems(data, containers=None):
    """返回所有子元素顺序违规的位置，形如 "pPr: keepNext 在 spacing 之后"。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    problems = []
    for container, order in ORDER.items():
        if containers is not None and container not in containers:
            continue
        for element in root.iter("{%s}%s" % (NS["w"], container)):
            last_index = -1
            last_name = None
            for child in element:
                name = local_name(child)
                if name not in order:
                    continue
                index = order.index(name)
                if index < last_index:
                    problems.append("%s: %s 在 %s 之后" % (container, name, last_name))
                else:
                    last_index = index
                    last_name = name
    return problems


PARAGRAPH_CONTAINERS = ("pPr", "rPr")
TABLE_CONTAINERS = ("tblPr", "tcPr", "trPr")


class ExportOoxmlTests(ExportFixture, TestCase):
    """The exported paper is a valid document, whatever the teacher asked for."""

    def _export(self, publications, fields, print_options=None, output_format="docx"):
        if not isinstance(publications, (list, tuple)):
            publications = [publications]
        payload = self.payload(publications, print_options=print_options, output_format=output_format)
        payload["rendered_fields"] = fields
        data, _filename, _mime, _count = export.export(payload)
        return data

    @staticmethod
    def _table_field(raw):
        """A table has to arrive as a table block; the plain text form is refused."""
        rows = export._table_source(raw, "合成表格")
        for row in rows:
            for cell in row:
                cell["segments"] = field(cell["source"])["blocks"][0]["segments"] if cell["source"] else []
        return {"source": raw, "blocks": [{"type": "table", "start": 0, "end": units(raw), "rows": rows}]}

    def _paper(self):
        plain = self.publication(stem="已知函数 f(x)=x^2，求 f(2) 的值。", answer="4", analysis="直接代入。")
        choice = self.publication(
            stem="下列说法正确的是（　　）",
            kind="single_choice",
            answer="A",
            options={"A": "第一项", "B": "第二项", "C": "第三项", "D": "第四项"},
        )
        figure = self.publication(stem="如图所示，求阴影部分面积。", answer="6", analysis="割补法。")
        self.figure(figure)
        raw_table = (
            "<table><tr><th>物块</th><th>质量</th></tr>"
            "<tr><td>甲</td><td>2 kg</td></tr><tr><td>乙</td><td>4 kg</td></tr></table>"
        )
        table = self.publication(stem=raw_table, answer="见解析", analysis="逐项比较。")
        fields = {}
        for publication in (plain, choice, figure, table):
            content = publication.content
            fields[str(publication.id)] = {
                "stem": (
                    self._table_field(content["stem"])
                    if content["stem"].lstrip().startswith("<table")
                    else field(content["stem"])
                ),
                "answer": field(content["answer"]),
                "analysis": field(content["analysis"]),
                "origin": field(content["origin"]),
            }
            for key, value in (content["options"] or {}).items():
                fields[str(publication.id)][f"options.{key}"] = field(value)
        return [plain, choice, figure, table], fields

    def test_paragraph_properties_keep_the_schema_order(self):
        """Both pagination paths write paragraph switches; both must stay in order."""
        publications, fields = self._paper()
        for pagination in ("compact", "keep"):
            for document in ("questions", "combined"):
                with self.subTest(pagination=pagination, document=document):
                    data = self._export(
                        publications, fields, print_options={"pagination": pagination, "document": document}
                    )
                    self.assertEqual(order_problems(data, PARAGRAPH_CONTAINERS), [], "段落属性顺序不合规")

    def test_table_properties_keep_the_schema_order(self):
        """Tables carry their own fixed sequence too (borders, cell widths, rows)."""
        publications, fields = self._paper()
        for layout in ("auto", "four"):
            with self.subTest(layout=layout):
                data = self._export(publications, fields, print_options={"option_layout": layout})
                self.assertEqual(order_problems(data, TABLE_CONTAINERS), [], "表格属性顺序不合规")

    def test_the_option_table_borders_sit_before_the_table_look(self):
        """The compact option table writes w:tblBorders; it has a fixed place."""
        choice = self.publication(
            stem="下列说法正确的是（　　）",
            kind="single_choice",
            answer="A",
            options={"A": "第一项", "B": "第二项", "C": "第三项", "D": "第四项"},
        )
        fields = {
            str(choice.id): {
                "stem": field(choice.content["stem"]),
                "answer": field("A"),
                "analysis": field(""),
                "origin": field(choice.content["origin"]),
                "options.A": field("第一项"),
                "options.B": field("第二项"),
                "options.C": field("第三项"),
                "options.D": field("第四项"),
            }
        }
        data = self._export(choice, fields, print_options={"option_layout": "four"})
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            root = etree.fromstring(archive.read("word/document.xml"))
        tables = list(root.iter("{%s}tbl" % NS["w"]))
        self.assertTrue(tables, "选择题应当排成一张表")
        for table in tables:
            names = [local_name(child) for child in table.find("{%s}tblPr" % NS["w"])]
            if "tblBorders" in names:
                self.assertLess(
                    names.index("tblBorders"), names.index("tblLook"), "w:tblBorders 必须排在 w:tblLook 之前"
                )

    def test_a_formula_the_converter_rejects_degrades_instead_of_losing_the_paper(self):
        """A formula the third-party converter cannot read must not fail the export.

        mathml2omml 0.0.2 raises AttributeError on a nested <math>; that is the
        library's opinion about the input, not a broken paper. Losing the whole
        paper over one formula is exactly what this module refuses to do.
        """
        inner = '<math xmlns="%s"><semantics><mi>x</mi></semantics></math>' % export.MATH_NS
        raw = (
            '<math xmlns="%s"><semantics>%s'
            '<annotation encoding="application/x-tex">x</annotation></semantics></math>' % (export.MATH_NS, inner)
        )
        segment = {"type": "math", "start": 0, "end": len(raw), "latex": "x", "mathml": raw, "display": False}
        self.assertIs(export._math(segment, "体检"), export._UNSUPPORTED)

    def test_a_normal_formula_stays_a_native_word_equation(self):
        """The degradation above must not catch ordinary formulas."""
        body = "<mi>x</mi>"
        segment = math_field("x", body)["blocks"][0]["segments"][0]
        self.assertIsNot(export._math(segment, "体检"), export._UNSUPPORTED)

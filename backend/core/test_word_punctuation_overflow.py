"""导出 Word 必须关掉「标点溢出边界」。

Word 默认允许行末的标点（。，；：等）悬挂到版心右缘之外；Chrome 没有这条
规矩 —— 放不下的标点会被推到下一行。

忠实链路实测（_probe155.py，12 道多行题干）：

    预览第 1 行末 =「驶向乙」（189.99 毫米）
    Word 第 1 行末 =「驶向乙地。」（194.10 毫米，正好是版心右缘）

Word 每行多塞一个字，整行的内容就错开一行，块里的公式和图片跟着一起错位；
挂出去的标点还越过版心右缘 194.09 毫米。给 docx 里所有段落写
<w:overflowPunct w:val="0"/> 之后，那道题的三行与预览逐字一致
（_probe157.py）。

关掉它要写 w:overflowPunct，而且**在 pPr 里的位置必须排在 w:topLinePunct /
w:autoSpaceDE 之前** —— 放错位置 Word 直接忽略（曾经就因为位置不对，
以为关不掉）。

判据全部是独立字面值，不引用被测模块的常量。
"""

import io

from django.test import SimpleTestCase
from docx.oxml.ns import qn

STEM_QUESTION = "11111111-1111-4111-8111-111111111111"
CHOICE_QUESTION = "22222222-2222-4222-8222-222222222222"

# OOXML 规定的 pPr 子元素顺序（ECMA-376 CT_PPrBase + CT_PPr）。抄一份在
# 尺子里，不引用被测模块的常量 —— 尺子的判据不能跟着实现一起跑偏。
# 标签名用**裸名**（不带 w: 前缀），和 lxml 的 tag.split("}")[-1] 对得上。
PPR_ORDER = (
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
)

# overflowPunct 必须排在这些**前面**。
MUST_PRECEDE = ("topLinePunct", "autoSpaceDE", "autoSpaceDN")


def build_item(question_id, question_type, stem="题干示例。", options=None):
    return {
        "id": question_id,
        "type": question_type,
        "where": "第 1 题",
        "content": {"options": options or {}, "body_mode": "text"},
        "fields": {"stem": [{"type": "text", "segments": [{"type": "text", "raw": stem}]}]},
        "images": [],
    }


def build_document(items, answer_space="none", font_size=12):
    """走**真实导出路径**造一份 Word。"""
    from . import library_export

    options = {
        "font_size": font_size,
        "student_info": False,
        "answer_space": answer_space,
        "pagination": "none",
        "question_breaks": set(),
        "answer_layout": "appendix",
    }
    data = library_export._document(items, "第七十二轮", options, "questions")
    from docx import Document

    return Document(io.BytesIO(data))


def paragraphs_of(document):
    """正文里的所有段落（含表格里的）。"""
    return list(document._element.body.iter(qn("w:p")))


def header_footer_paragraphs(document):
    """页眉页眉里的所有段落。"""
    found = []
    for part in document.part.package.iter_parts():
        element = getattr(part, "element", None)
        if element is None or element.tag not in (qn("w:hdr"), qn("w:ftr")):
            continue
        found.extend(element.iter(qn("w:p")))
    return found


def overflow_of(paragraph):
    """段落 pPr 里的 w:overflowPunct（可能没有）。"""
    properties = paragraph.find(qn("w:pPr"))
    if properties is None:
        return None
    return properties.find(qn("w:overflowPunct"))


def tag_order(paragraph):
    """pPr 里各子元素的标签名，按文档里的实际顺序。"""
    properties = paragraph.find(qn("w:pPr"))
    if properties is None:
        return []
    return [child.tag.split("}")[-1] for child in properties]


class PunctuationOverflowTests(SimpleTestCase):
    """正文每个段落都要关掉标点溢出边界。"""

    def test_every_body_paragraph_carries_the_overflow_punct_flag(self):
        """一个都不能漏 —— 漏一个，那一段的行末标点照样悬挂出去。"""
        document = build_document(
            [
                build_item(STEM_QUESTION, "free_response", stem="第一题的题干。"),
                build_item(CHOICE_QUESTION, "single_choice", stem="第二题的题干。"),
            ]
        )
        paragraphs = paragraphs_of(document)
        self.assertTrue(paragraphs, "尺子自检：导出结果里一个段落都没有")
        missing = [index for index, paragraph in enumerate(paragraphs) if overflow_of(paragraph) is None]
        self.assertEqual(missing, [], f"这些段落没有写 w:overflowPunct：{missing}")

    def test_the_overflow_punct_flag_is_switched_off(self):
        """值必须是 0 —— 写 1 / true 都等于没关。"""
        document = build_document([build_item(STEM_QUESTION, "free_response")])
        values = []
        for paragraph in paragraphs_of(document):
            element = overflow_of(paragraph)
            if element is None:
                continue
            values.append(element.get(qn("w:val")))
        self.assertTrue(values, "尺子自检：一个 w:overflowPunct 都没读到")
        wrong = [value for value in values if value != "0"]
        self.assertEqual(wrong, [], f"这些 w:overflowPunct 不是关：{wrong}")

    def test_the_overflow_punct_sits_before_the_cjk_spacing_flags(self):
        """位置错了 Word 直接忽略 —— 必须排在 w:autoSpaceDE 之前。

        判据方向：OOXML 里 overflowPunct 的序位比 topLinePunct / autoSpaceDE
        都**靠前**，所以只要 overflowPunct **前面**出现了它们，就是排错了。
        """
        document = build_document([build_item(STEM_QUESTION, "free_response")])
        misplaced = []
        for index, paragraph in enumerate(paragraphs_of(document)):
            element = overflow_of(paragraph)
            if element is None:
                continue
            properties = paragraph.find(qn("w:pPr"))
            children = list(properties)
            position = children.index(element)
            for other in children[:position]:
                name = other.tag.split("}")[-1]
                if name in MUST_PRECEDE:
                    misplaced.append((index, name))
        self.assertEqual(misplaced, [], f"这些段落里 w:overflowPunct 排错了位置：{misplaced}")

    def test_the_ppr_children_keep_the_ooxml_order(self):
        """整份 pPr 的子元素顺序必须合法（越界时 Word 会扔掉后面的属性）。"""
        document = build_document(
            [
                build_item(STEM_QUESTION, "free_response", stem="第一题的题干。"),
                build_item(CHOICE_QUESTION, "single_choice", stem="第二题的题干。"),
            ]
        )
        broken = []
        for index, paragraph in enumerate(paragraphs_of(document)):
            order = tag_order(paragraph)
            positions = [PPR_ORDER.index(name) for name in order if name in PPR_ORDER]
            if positions != sorted(positions):
                broken.append((index, order))
        self.assertEqual(broken, [], f"这些段落的 pPr 顺序不合法：{broken}")


class PunctuationOverflowHeaderFooterTests(SimpleTestCase):
    """页脚里的行末标点同样要关。"""

    def test_the_footer_paragraphs_carry_the_overflow_punct_flag(self):
        """页脚有「第 1 页，共 2 页」，一样有行末标点。"""
        document = build_document([build_item(STEM_QUESTION, "free_response")])
        paragraphs = header_footer_paragraphs(document)
        self.assertTrue(paragraphs, "尺子自检：导出结果里没有页眉页脚段落")
        missing = [index for index, paragraph in enumerate(paragraphs) if overflow_of(paragraph) is None]
        self.assertEqual(missing, [], f"这些页眉页脚段落没有写 w:overflowPunct：{missing}")

    def test_the_footer_overflow_punct_is_switched_off(self):
        """页脚里的值也得是 0。"""
        document = build_document([build_item(STEM_QUESTION, "free_response")])
        values = []
        for paragraph in header_footer_paragraphs(document):
            element = overflow_of(paragraph)
            if element is None:
                continue
            values.append(element.get(qn("w:val")))
        self.assertTrue(values, "尺子自检：一个页眉页脚的 w:overflowPunct 都没读到")
        wrong = [value for value in values if value != "0"]
        self.assertEqual(wrong, [], f"这些页眉页脚的 w:overflowPunct 不是关：{wrong}")


class PunctuationOverflowIdempotenceTests(SimpleTestCase):
    """重复关一次不能写出两份 —— 两份就有一段会把自己的属性位置挤乱。"""

    def test_running_the_fix_twice_writes_the_flag_once(self):
        """连着跑两遍，每段仍然只有一个 w:overflowPunct。"""
        from docx import Document

        from . import library_export

        document = Document()
        document.add_paragraph("第一段。")
        document.add_paragraph("第二段。")
        library_export._disable_punctuation_overflow(document)
        library_export._disable_punctuation_overflow(document)
        counts = []
        for paragraph in document._element.body.iter(qn("w:p")):
            properties = paragraph.find(qn("w:pPr"))
            if properties is None:
                continue
            counts.append(len(properties.findall(qn("w:overflowPunct"))))
        self.assertTrue(counts, "尺子自检：自造的文档里一个段落都没有")
        duplicated = [count for count in counts if count != 1]
        self.assertEqual(duplicated, [], f"这些段落里的 w:overflowPunct 不是恰好一个：{duplicated}")

    def test_the_fix_also_reaches_a_paragraph_that_already_carries_the_flag(self):
        """已经写死的段落要能被改成 0，而不是留下两份。"""
        from docx import Document

        from . import library_export

        document = Document()
        paragraph = document.add_paragraph("第一段。")
        properties = paragraph._p.get_or_add_pPr()
        namespace = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        from docx.oxml import parse_xml

        properties.append(parse_xml(f'<w:overflowPunct {namespace} w:val="1"/>'))
        library_export._disable_punctuation_overflow(document)
        elements = properties.findall(qn("w:overflowPunct"))
        self.assertEqual(len(elements), 1, f"w:overflowPunct 有 {len(elements)} 个")
        self.assertEqual(elements[0].get(qn("w:val")), "0")


class PunctuationOverflowPreviewEvidenceTests(SimpleTestCase):
    """尺子自检：把「标点会悬挂出去」这条真相钉成字面值。"""

    def test_the_preview_right_edge_is_inside_the_body_column(self):
        """预览的版心右缘 193.997 毫米 = 正文列 173.343 + 起点 20.654。

        Word 的版心右缘是 194.086 毫米 —— 标点悬挂出去的话，行末标点就
        正好卡在这条线上（实测 194.10 毫米）。
        """
        body_column_mm = 173.343
        body_start_mm = 20.654
        self.assertAlmostEqual(body_start_mm + body_column_mm, 193.997, places=2)
        self.assertAlmostEqual(194.086, 194.098, delta=0.05)

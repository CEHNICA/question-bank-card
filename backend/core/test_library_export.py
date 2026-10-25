"""Offline export contract, native Word structures and fail-closed downloads."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import tempfile
import uuid
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from django.test import Client, TestCase, override_settings
from django.urls import path
from lxml import etree
from PIL import Image

from . import features, library, library_drafts, library_export as export
from .models import Paper, PublishedQuestion, Question

urlpatterns = [path("api/library/export-docx", export.export_docx_view)]
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": export.OMML_NS,
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}


def units(value):
    return len(value.encode("utf-16-le")) // 2


def field(value):
    return {
        "source": value,
        "blocks": [
            {
                "type": "text",
                "start": 0,
                "end": units(value),
                "segments": [{"type": "text", "start": 0, "end": units(value)}],
            }
        ]
        if value
        else [],
    }


def math_field(latex, body, *, display=False):
    source = ("$$" if display else "$") + latex + ("$$" if display else "$")
    mathml = (
        f'<math xmlns="{export.MATH_NS}"><semantics>{body}<annotation encoding="application/x-tex">'
        + __import__("html").escape(latex)
        + "</annotation></semantics></math>"
    )
    return {
        "source": source,
        "blocks": [
            {
                "type": "text",
                "start": 0,
                "end": units(source),
                "segments": [
                    {
                        "type": "math",
                        "start": 0,
                        "end": units(source),
                        "latex": latex,
                        "mathml": mathml,
                        "display": display,
                    }
                ],
            }
        ],
    }


def document_xml(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return etree.fromstring(archive.read("word/document.xml"))


def tree_paras(tree):
    """Every top-level paragraph, which is where the header lines live."""
    return tree.find("w:body", NS).findall("w:p", NS)


class ExportFixture:
    """Publication/payload/post helpers, shared with the opt-in browser module.

    Not a TestCase on its own: test_library_export_browser reuses these without
    re-running every assertion here.
    """

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        settings = override_settings(DATA_ROOT=self.root)
        settings.enable()
        self.addCleanup(settings.disable)
        environment = mock.patch.dict(os.environ, {"QB_FEATURES_FILE": str(self.root / "features.json")})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = Client()
        self.counter = 0

    def publication(
        self,
        *,
        stem="离线合成题",
        kind="free_response",
        answer="",
        analysis="",
        options=None,
        origin="虚构出处",
        status="published",
        extras=None,
        section="",
    ):
        self.counter += 1
        paper = Paper.objects.create(filename="测试合成卷.pdf", kind="pdf", sha256="a" * 64)
        question = Question.objects.create(
            paper=paper,
            number=self.counter,
            question_type=kind,
            stem=stem,
            answer=answer,
            analysis=analysis,
            options=options or {},
            state="green",
        )
        content = {
            "stem": stem,
            "answer": answer,
            "analysis": analysis,
            "options": options or {},
            "question_type": kind,
            "origin": origin,
            "figures": [],
            "sources": [],
            "section": section,
            "document_id": str(paper.id),
            "source_filename": paper.filename,
        }
        return PublishedQuestion.objects.create(
            paper=paper,
            question=question,
            number=self.counter,
            source_filename=paper.filename,
            question_type=kind,
            version=1,
            status=status,
            content=content,
            content_hash=library.content_hash(content),
            extras=extras or {},
        )

    def payload(self, pubs, *, print_options=None, output_format="docx", title="单元练习"):
        options = library_drafts._print_options(print_options or {})
        fields = {}
        for publication in pubs:
            content = publication.content
            selected, _ = export._selected(
                content, publication.extras, options["ai_answers"] and features.enabled("ai_answer")
            )
            fields[str(publication.id)] = {name: field(str(content.get(name) or "")) for name in ("stem", "origin")}
            fields[str(publication.id)].update(
                {f"options.{key}": field(value) for key, value in (content.get("options") or {}).items()}
            )
            fields[str(publication.id)].update(
                {name: field(str(selected.get(name) or "")) for name in ("answer", "analysis")}
            )
        return {
            "ids": [str(pub.id) for pub in pubs],
            "title": title,
            "print_options": print_options or {},
            "rendered_fields": fields,
            "format": output_format,
        }

    def post(self, payload, **changes):
        return self.client.post(
            "/api/library/export-docx",
            json.dumps(payload),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
            **changes,
        )

    def figure(self, publication, slot="stem", color="red"):
        folder = self.root / "library" / str(publication.id)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "figure-1.png"
        Image.new("RGB", (240, 120), color).save(target)
        publication.content["figures"] = [{"slot": slot, "file": target.name, "page_idx": 0, "bbox": [0, 0, 100, 100]}]
        publication.content_hash = library.content_hash(publication.content)
        publication.save(update_fields=["content", "content_hash"])
        return target


@override_settings(ROOT_URLCONF=__name__)
class LibraryExportTests(ExportFixture, TestCase):
    def test_docx_is_complete_editable_and_does_not_write_question_data(self):
        first = self.publication(kind="free_response", answer="2", analysis="离线解析")
        second = self.publication(
            kind="single_choice", answer="A", options={"A": "甲", "B": "乙", "C": "丙", "D": "丁"}
        )
        self.figure(first)
        before = list(PublishedQuestion.objects.values())
        questions = list(Question.objects.values())
        result = self.post(
            self.payload(
                [first, second],
                print_options={"document": "combined", "origin": True, "font_size": 14, "answer_space": "large"},
            )
        )
        self.assertEqual(result.status_code, 200, result.content[:300])
        self.assertEqual(result["X-Question-Count"], "2")
        self.assertIn("filename*=UTF-8''", result["Content-Disposition"])
        self.assertEqual(result["Cache-Control"], "no-store")
        self.assertEqual(
            result["Content-Type"], "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        xml = document_xml(result.content)
        text = "".join(xml.itertext())
        self.assertIn("姓名", text)
        self.assertIn("得分", text)
        self.assertIn("虚构出处", text)
        self.assertIn("离线解析", text)
        self.assertLess(text.index("选择题"), text.index("解答题"))
        self.assertEqual(len(xml.findall(".//a:blip", NS)), 1)
        self.assertTrue(xml.findall(".//w:spacing[@w:after='3402']", NS))  # 60 mm
        with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
            self.assertEqual(len([name for name in archive.namelist() if name.startswith("word/media/")]), 1)
        self.assertEqual(list(PublishedQuestion.objects.values()), before)
        self.assertEqual(list(Question.objects.values()), questions)

    def test_the_original_section_heading_is_not_printed_but_the_short_chapter_name_is(self):
        # 1.26.25：这一行以前印的是原卷那一整行（「一、选择题：本题共 12 小题…」），
        # 屏幕上印的却是「一、选择题」—— 老师核对的是屏幕，拿到手的是文件，两边对不上。
        # 现在两边都印题型名：那一行带着原卷的题数、分值和题号范围，跟老师挑的这几道题对不上。
        printed = (
            "一、选择题：本题共 12 小题，每小题 5 分，共 60 分，在每小题给出的四个选项中，只有一项是符合题目要求的"
        )
        first = self.publication(kind="single_choice", options={"A": "甲", "B": "乙"}, section=printed)
        second = self.publication(kind="single_choice", options={"A": "甲", "B": "乙"}, section=printed)
        result = self.post(self.payload([first, second]))
        self.assertEqual(result.status_code, 200, result.content[:300])
        text = "".join(document_xml(result.content).itertext())
        self.assertIn("一、选择题", text)
        self.assertNotIn(printed, text)
        self.assertNotIn("共 60 分", text)
        self.assertNotIn("本题共", text)

        # 教材那种短章节名说的是「这些题出自哪一节」，对新卷子依然成立，照旧印。
        third = self.publication(kind="single_choice", options={"A": "甲", "B": "乙"}, section="习题 3.1")
        fourth = self.publication(kind="single_choice", options={"A": "甲", "B": "乙"}, section="习题 3.1")
        result = self.post(self.payload([third, fourth]))
        self.assertEqual(result.status_code, 200, result.content[:300])
        text = "".join(document_xml(result.content).itertext())
        self.assertIn("习题 3.1", text)

    def test_a_mixed_group_falls_back_to_the_generic_heading(self):
        # 组卷可以凑好几份卷子的题，这时原标题互相矛盾，只能用通用说法。
        first = self.publication(
            kind="single_choice", options={"A": "甲", "B": "乙"}, section="一、选择题（每小题4分共40分）"
        )
        second = self.publication(
            kind="single_choice", options={"A": "甲", "B": "乙"}, section="一、选择题（每小题5分共50分）"
        )
        third = self.publication(kind="single_choice", options={"A": "甲", "B": "乙"})
        result = self.post(self.payload([first, second, third]))
        self.assertEqual(result.status_code, 200, result.content[:300])
        text = "".join(document_xml(result.content).itertext())
        self.assertIn("一、选择题", text)
        self.assertNotIn("共40分", text)
        self.assertNotIn("共50分", text)

        # 没有标题、过长的标题都退回通用说法。
        self.assertEqual(export._section_heading([{"content": {"section": ""}}], 1, "填空题"), "二、填空题")
        self.assertEqual(export._section_heading([{"content": {"section": "习" * 300}}], 2, "填空题"), "三、填空题")
        # 大题说明和短章节名混在一起：长说明是噪音，不印；一句话说不准，用题型名。
        self.assertEqual(
            export._section_heading(
                [{"content": {"section": "一、选择题：本题共 12 小题"}}, {"content": {"section": "习题 3.1"}}],
                0,
                "选择题",
            ),
            "一、选择题",
        )
        # 只有一种短章节名时照旧印它。
        self.assertEqual(
            export._section_heading(
                [{"content": {"section": "习题 3.1"}}, {"content": {"section": "习题 3.1"}}], 0, "选择题"
            ),
            "习题 3.1",
        )

    def test_questions_only_omits_answer_fields_and_answer_pages(self):
        pub = self.publication(answer="不要出现在题目卷")
        payload = self.payload([pub], print_options={"answers": False, "student_info": False})
        del payload["rendered_fields"][str(pub.id)]["answer"]
        del payload["rendered_fields"][str(pub.id)]["analysis"]
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        text = "".join(document_xml(result.content).itertext())
        self.assertNotIn("不要出现在题目卷", text)
        self.assertNotIn("参考答案", text)
        self.assertNotIn("姓名", text)

    def test_combined_without_selected_answers_has_no_empty_appendix_or_page_break(self):
        blank = self.publication(stem="无答案合卷第一题", answer=" \n ", analysis="\t ")
        optional_ai = self.publication(stem="无答案合卷第二题", extras={"ai_answer": {"answer": "未选用的 AI 参考"}})
        before = list(PublishedQuestion.objects.values())
        payload = self.payload([blank, optional_ai], print_options={"document": "combined", "answers": True})
        result = self.post(payload)
        self.assertEqual(result.status_code, 200, result.content[:300])
        xml = document_xml(result.content)
        text = "".join(xml.itertext())
        self.assertIn("无答案合卷第一题", text)
        self.assertIn("无答案合卷第二题", text)
        self.assertNotIn("参考答案与解析", text)
        self.assertNotIn("原卷未提供答案", text)
        self.assertNotIn("未选用的 AI 参考", text)
        self.assertFalse(xml.findall(".//w:br[@w:type='page']", NS))
        self.assertEqual(list(PublishedQuestion.objects.values()), before)

    def test_combined_with_some_answers_keeps_all_numbers_and_analysis_only_content(self):
        missing = self.publication(stem="题目一")
        answered = self.publication(stem="题目二", answer="原卷结果二")
        explained = self.publication(stem="题目三", analysis="原卷仅有解析三")
        response = self.post(self.payload([missing, answered, explained], print_options={"document": "combined"}))
        self.assertEqual(response.status_code, 200, response.content[:300])
        xml = document_xml(response.content)
        text = "".join(xml.itertext())
        self.assertIn("参考答案与解析", text)
        self.assertIn("1.（原卷未提供答案）", text)
        self.assertIn("2. 原卷结果二", text)
        self.assertIn("原卷仅有解析三", text)
        self.assertEqual(len(xml.findall(".//w:br[@w:type='page']", NS)), 1)

    def test_explicit_answers_document_wins_over_legacy_answers_false(self):
        pub = self.publication(answer="参考结果", stem="题干不应印")
        payload = self.payload([pub], print_options={"answers": False, "document": "answers"})
        del payload["rendered_fields"][str(pub.id)]["stem"]
        del payload["rendered_fields"][str(pub.id)]["origin"]
        response = self.post(payload)
        self.assertEqual(response.status_code, 200)
        text = "".join(document_xml(response.content).itertext())
        self.assertIn("参考结果", text)
        self.assertNotIn("题干不应印", text)

    def test_split_zip_contains_two_full_documents_without_enabling_ai(self):
        # 答案与解析用一句不会出现在题干里的长句：分卷靠 _document 按 document 分流，
        # 题目卷和答案卷读的是同一份 captured，只有一处改错答案就上学生卷了。
        # 原来的断言只查「参考答案与解析」这个标题，答案串又短到两个字，漏了也测不出来。
        pub = self.publication(
            stem="离线合成题干甲乙丙",
            answer="原卷标准答案甲乙丙",
            analysis="原卷解析步骤甲乙丙",
            extras={"ai_answer": {"answer": "不同AI答案甲乙丙"}},
        )
        response = self.post(self.payload([pub], output_format="split", print_options={"document": "questions"}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(len(archive.namelist()), 2)
            questions = "".join(document_xml(archive.read("单元练习-题目卷.docx")).itertext())
            answers = "".join(document_xml(archive.read("单元练习-答案解析卷.docx")).itertext())
        self.assertNotIn("参考答案与解析", questions)
        # 学生卷上只有题干：答案、解析、AI 参考，一个字都不能有。
        self.assertIn("离线合成题干甲乙丙", questions)
        self.assertNotIn("原卷标准答案甲乙丙", questions)
        self.assertNotIn("原卷解析步骤甲乙丙", questions)
        self.assertNotIn("不同AI答案甲乙丙", questions)
        # 答案卷上三样都要在，题干不该重复印一遍。
        self.assertIn("原卷标准答案甲乙丙", answers)
        self.assertIn("原卷解析步骤甲乙丙", answers)
        self.assertNotIn("离线合成题干甲乙丙", answers)
        self.assertFalse(features.enabled("ai_answer"))

    def test_empty_answers_or_split_is_an_explicit_error(self):
        pub = self.publication()
        for options, mode in (({"document": "answers"}, "docx"), ({}, "split")):
            result = self.post(self.payload([pub], print_options=options, output_format=mode))
            self.assertEqual(result.status_code, 400)
            self.assertIn("没有原卷答案", result.json()["error"])

    def test_ai_is_off_by_default_and_requires_both_explicit_choices(self):
        pub = self.publication(extras={"ai_answer": {"answer": "AI合成参考", "analysis": "合成解析"}})
        result = self.post(self.payload([pub], print_options={"document": "combined", "ai_answers": True}))
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("AI合成参考", "".join(document_xml(result.content).itertext()))
        features.save({"ai_answer": True})
        result = self.post(self.payload([pub]))
        self.assertNotIn("AI合成参考", "".join(document_xml(result.content).itertext()))
        result = self.post(self.payload([pub], print_options={"document": "combined", "ai_answers": True}))
        text = "".join(document_xml(result.content).itertext())
        self.assertIn("AI合成参考", text)
        self.assertIn("AI 参考 · 未核对", text)
        pub.refresh_from_db()
        self.assertEqual(pub.content["answer"], "")

    def test_original_answer_wins_and_original_analysis_is_last_fallback(self):
        features.save({"ai_answer": True})
        pub = self.publication(answer="原卷答案", extras={"ai_answer": {"answer": "AI答案"}})
        result = self.post(self.payload([pub], print_options={"document": "combined", "ai_answers": True}))
        text = "".join(document_xml(result.content).itertext())
        self.assertIn("原卷答案", text)
        self.assertNotIn("AI答案", text)
        pub.content["answer"] = ""
        pub.content["analysis"] = "原卷解析"
        pub.save(update_fields=["content"])
        result = self.post(self.payload([pub], print_options={"document": "combined", "ai_answers": False}))
        self.assertIn("原卷解析", "".join(document_xml(result.content).itertext()))
        result = self.post(self.payload([pub], print_options={"document": "combined", "ai_answers": True}))
        self.assertIn("AI答案", "".join(document_xml(result.content).itertext()))

    def test_unavailable_ids_abort_whole_export_and_never_substitute_new_version(self):
        valid = self.publication()
        for status in ("withdrawn", "superseded"):
            old = self.publication(status=status)
            result = self.post(self.payload([valid, old]))
            self.assertEqual(result.status_code, 409)
            self.assertIn("第 2 题", result.json()["error"])
        payload = self.payload([valid])
        key = str(uuid.uuid4())
        payload["ids"].append(key)
        payload["rendered_fields"][key] = {}
        self.assertEqual(self.post(payload).status_code, 409)

    def test_source_mismatch_and_missing_fields_are_identified(self):
        pub = self.publication()
        payload = self.payload([pub])
        payload["rendered_fields"][str(pub.id)]["stem"] = field("客户端改写")
        response = self.post(payload)
        self.assertEqual(response.status_code, 409)
        self.assertIn("stem", response.json()["error"])
        payload = self.payload([pub])
        del payload["rendered_fields"][str(pub.id)]["stem"]
        self.assertEqual(self.post(payload).status_code, 400)

    def test_utf16_astral_offsets_accept_whole_characters_and_reject_split(self):
        pub = self.publication(stem="合成😀题")
        payload = self.payload([pub])
        self.assertEqual(self.post(payload).status_code, 200)
        block = payload["rendered_fields"][str(pub.id)]["stem"]["blocks"][0]
        block["segments"] = [{"type": "text", "start": 0, "end": 3}, {"type": "text", "start": 3, "end": 5}]
        response = self.post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("切开", response.json()["error"])

    def test_gaps_and_omitting_non_delimiter_text_are_rejected(self):
        pub = self.publication(stem="测试合成原文")
        for edit in (
            lambda seg: seg.update(end=2),
            lambda seg: seg.update(type="delimiter"),
            lambda seg: seg.update(start=1),
            lambda seg: seg.update(auto=True),
        ):
            payload = self.payload([pub])
            edit(payload["rendered_fields"][str(pub.id)]["stem"]["blocks"][0]["segments"][0])
            self.assertEqual(self.post(payload).status_code, 400)

    def test_special_symbols_and_blank_are_editable_text_and_cover_original(self):
        source = "$▱ABCD$ ___ （ ）"
        pub = self.publication(stem=source)
        value = {
            "source": source,
            "blocks": [
                {
                    "type": "text",
                    "start": 0,
                    "end": units(source),
                    "segments": [
                        {"type": "delimiter", "start": 0, "end": 1},
                        {"type": "parallelogram", "start": 1, "end": 2},
                        {"type": "text", "start": 2, "end": 6},
                        {"type": "delimiter", "start": 6, "end": 7},
                        {"type": "text", "start": 7, "end": 8},
                        {"type": "blank", "start": 8, "end": 11},
                        {"type": "text", "start": 11, "end": 12},
                        {"type": "bracket", "start": 12, "end": 15},
                    ],
                }
            ],
        }
        payload = self.payload([pub])
        payload["rendered_fields"][str(pub.id)]["stem"] = value
        result = self.post(payload)
        self.assertEqual(result.status_code, 200, result.content[:300])
        text = "".join(document_xml(result.content).itertext())
        self.assertIn("▱ABCD", text)
        self.assertIn("________", text)
        self.assertNotIn("$", text)

    def test_square_root_and_fraction_become_native_editable_omml(self):
        value = math_field(
            r"\sqrt{x+1}+\frac{a}{b}",
            "<mrow><msqrt><mrow><mi>x</mi><mo>+</mo><mn>1</mn></mrow></msqrt><mo>+</mo><mfrac><mi>a</mi><mi>b</mi></mfrac></mrow>",
            display=True,
        )
        pub = self.publication(stem=value["source"])
        payload = self.payload([pub])
        payload["rendered_fields"][str(pub.id)]["stem"] = value
        result = self.post(payload)
        self.assertEqual(result.status_code, 200, result.content[:300])
        xml = document_xml(result.content)
        self.assertEqual(len(xml.findall(".//m:oMath", NS)), 1)
        self.assertEqual(len(xml.findall(".//m:f", NS)), 1)
        rad = xml.find(".//m:rad", NS)
        self.assertIsNotNone(rad.find("m:deg", NS))
        self.assertEqual(rad.find("m:radPr/m:degHide", NS).get(f"{{{export.OMML_NS}}}val"), "1")
        self.assertIn("x+1", "".join(rad.itertext()))
        self.assertNotIn("\\sqrt", "".join(xml.itertext()))

    def test_stretchy_arrow_known_upstream_schema_typo_is_repaired(self):
        value = math_field(
            r"\overrightarrow{AB}", '<mover><mrow><mi>A</mi><mi>B</mi></mrow><mo stretchy="true">→</mo></mover>'
        )
        converted = export._field(value, value["source"], "合成公式")
        math = converted[0]["segments"][0]["omml"]
        self.assertTrue(math.findall(".//m:groupChrPr", NS))
        self.assertIn("AB", "".join(math.itertext()))

    def test_vector_hat_bar_and_nested_accents_are_native_accents(self):
        for command, glyph in (
            ("vec", "⃗"),
            ("hat", "^"),
            ("bar", "ˉ"),
            ("dot", "˙"),
            ("tilde", "~"),
            ("overrightarrow", "→"),
        ):
            value = math_field(
                "\\" + command + "{AB}",
                f'<mover accent="true"><mrow><mi>A</mi><mi>B</mi></mrow><mo>{glyph}</mo></mover>',
            )
            math = export._field(value, value["source"], "合成重音")[0]["segments"][0]["omml"]
            accents = math.findall(".//m:acc", NS)
            self.assertEqual(len(accents), 1)
            self.assertEqual(
                accents[0].find("m:accPr/m:chr", NS).get(f"{{{export.OMML_NS}}}val"), export.ACCENTS[glyph]
            )
            self.assertFalse(math.findall(".//m:limUpp", NS))
            self.assertIn("AB", "".join(math.itertext()))
        value = math_field(
            r"\hat{\vec{x}}", '<mover accent="true"><mover accent="true"><mi>x</mi><mo>⃗</mo></mover><mo>^</mo></mover>'
        )
        math = export._field(value, value["source"], "合成重音")[0]["segments"][0]["omml"]
        self.assertEqual(len(math.findall(".//m:acc", NS)), 2)
        # A genuine overscript is a limit, not an accent.
        value = math_field(r"\overset{a}{x}", "<mover><mi>x</mi><mi>a</mi></mover>")
        math = export._field(value, value["source"], "合成重音")[0]["segments"][0]["omml"]
        self.assertTrue(math.findall(".//m:limUpp", NS))
        self.assertFalse(math.findall(".//m:acc", NS))

    def test_cancellation_is_never_silently_turned_into_a_box(self):
        """上游会把每个 menclose 都画成方框，划消就丢了。

        不管走哪条路都不能出现那个方框：Word 能转就转原生公式，转不了就降级成
        图片，绝不能悄悄把「划掉」印成「框起来」。
        """
        for command, notation in (
            ("cancel", "updiagonalstrike"),
            ("bcancel", "downdiagonalstrike"),
            ("xcancel", "updiagonalstrike downdiagonalstrike"),
        ):
            # Exact MathML enclosure shape produced by standard KaTeX.
            value = math_field("\\" + command + "{x}", f'<menclose notation="{notation}"><mi>x</mi></menclose>')
            segment = export._field(value, value["source"], "合成划消公式")[0]["segments"][0]
            self.assertIsNone(segment["omml"], f"{command} 不该被转成一个 Word 公式")
            self.assertIn("合成划消公式", segment["fallback"])
        boxed = math_field(r"\boxed{x}", '<menclose notation="box"><mi>x</mi></menclose>')
        self.assertTrue(
            export._field(boxed, boxed["source"], "合成方框")[0]["segments"][0]["omml"].findall(".//m:borderBox", NS)
        )

    def test_common_high_school_math_latex_corpus_becomes_native_editable_omml(self):
        r"""常见高中数学公式使用生产版 KaTeX 输出的 MathML 转成可编辑 Word 公式。

        语料由 tools\build_mathml_corpus.js 用仓库自带的 KaTeX 渲染，所以跑的是
        生产环境真正的 MathML 形状，不是手写的理想形状。漏掉一个字形（\overline 的
        U+203E、\acute 的 U+02CA、\grave 的 U+02CB）就会在这里变红。
        """
        corpus = json.loads((Path(export.__file__).parent / "test_mathml_corpus.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(corpus), 40, "语料本身要够大，否则这道闸门没有意义")
        for item in corpus:
            latex, mathml = item["latex"], item["mathml"]
            with self.subTest(latex=latex):
                # 语料存的是 KaTeX 完整的 <math>…</math>，前端就是这样送上来的。
                source = "$" + latex + "$"
                value = {
                    "source": source,
                    "blocks": [
                        {
                            "type": "text",
                            "start": 0,
                            "end": units(source),
                            "segments": [
                                {
                                    "type": "math",
                                    "start": 0,
                                    "end": units(source),
                                    "latex": latex,
                                    "mathml": mathml,
                                    "display": False,
                                }
                            ],
                        }
                    ],
                }
                segment = export._field(value, source, "语料")[0]["segments"][0]
                omml = segment["omml"]
                self.assertIsNotNone(omml, "这条公式没有转成原生公式")
                self.assertEqual(etree.QName(omml).localname, "oMath")
                self.assertTrue(list(omml), "转出来是空的")
                # 原生公式，不是把 LaTeX 源码塞进去。
                self.assertNotIn("\\", "".join(omml.itertext()))

    def test_a_function_name_becomes_a_function_so_word_keeps_the_gap(self):
        r"""``\sin x`` 在 Word 纸上不能印成 ``sinx``。

        mathml2omml 把函数名和参数之间留成一个**空 run**，Word 不会给空 run 补
        空格，于是 ``(e^x+2\sin x)/(1+x^2)`` 里的 ``\sin`` 紧贴 ``x``，学生会当成
        一个变量名。PDF 路线由 KaTeX 加空格，两条路线于是印得不一样。
        改成 ``m:func`` 之后细空格由 Word 自己补。

        参数只能取到下一个算符为止：``\sin\alpha+\cos\beta`` 里 ``\sin`` 的参数是
        ``\alpha``，不能把 ``+\cos\beta`` 一起吞进去。
        """
        corpus = json.loads((Path(export.__file__).parent / "test_mathml_corpus.json").read_text(encoding="utf-8"))
        wanted = {
            "\\sin\\alpha+\\cos\\beta",
            "\\ln x",
            "\\hat{y}=\\ln x",
            "\\lim_{x\\to 0}\\frac{\\sin x}{x}",
            "\\log_{2}8",
        }
        checked = 0
        for item in corpus:
            latex = item["latex"]
            if latex not in wanted:
                continue
            checked += 1
            with self.subTest(latex=latex):
                source = "$" + latex + "$"
                value = {
                    "source": source,
                    "blocks": [
                        {
                            "type": "text",
                            "start": 0,
                            "end": units(source),
                            "segments": [
                                {
                                    "type": "math",
                                    "start": 0,
                                    "end": units(source),
                                    "latex": latex,
                                    "mathml": item["mathml"],
                                    "display": False,
                                }
                            ],
                        }
                    ],
                }
                omml = export._field(value, source, "语料")[0]["segments"][0]["omml"]

                functions = omml.findall(".//m:func", NS)
                self.assertTrue(functions, f"{latex} 里一个 m:func 都没有，纸上会印成 sinx")
                for function in functions:
                    holder = function.find("m:fName", NS)
                    # 函数名取 fName 里第一个字；``\log_{2}`` 的下标 2 在 sSub 里，
                    # 用 itertext 会把它一起拼进来。
                    first = holder.find(".//m:t", NS)
                    name = (first.text or "").strip() if first is not None else ""
                    self.assertIn(name, export._FUNCTION_NAMES, f"{latex} 里 {name} 被当成了函数")
                    argument = function.find("m:e", NS)
                    self.assertIsNotNone(argument, f"{latex} 里 {name} 没有参数")
                    parts = ["".join(node.itertext()) for node in argument]
                    self.assertTrue(any(part.strip() for part in parts), f"{latex} 里 {name} 的参数是空的")
                    # 参数只能取到下一个算符为止，不能把 ``+\cos\beta`` 一起吞进去。
                    self.assertNotIn("+", parts, f"{latex} 里 {name} 的参数吞了后面的加号")
                # 一个函数名都不该再留在 m:func 外面（m:fName 里当然还有）。
                # 不能用 id() 比：lxml 每次访问都会新建代理对象，同一个节点的 id 不同。
                outside = [
                    "".join(node.itertext()).strip()
                    for node in omml.iter(f"{{{NS['m']}}}r")
                    if node.find("m:t", NS) is not None
                    and not any(parent.tag == f"{{{NS['m']}}}func" for parent in node.iterancestors())
                    and (node.find("m:t", NS).text or "").strip() in export._FUNCTION_NAMES
                ]
                self.assertEqual(outside, [], f"{latex} 里还有函数名没被收进 m:func")
        self.assertGreaterEqual(checked, 4, "语料里没有足够的函数公式，这条闸门没意义")

    def test_marking_a_function_changes_the_structure_and_not_a_single_character(self):
        """参数边界判错会吃掉字：``\\sin x + 1`` 印成 ``\\sin(x+1)``。

        所以改写前后 ``itertext()`` 必须一模一样。空 run 本来就没有文字，把它并进
        ``m:func`` 不影响这句话；真库 1267 条公式跑下来 0 处变化。
        """
        corpus = json.loads((Path(export.__file__).parent / "test_mathml_corpus.json").read_text(encoding="utf-8"))
        functions = 0
        for item in corpus:
            latex, mathml = item["latex"], item["mathml"]
            source = "$" + latex + "$"
            value = {
                "source": source,
                "blocks": [
                    {
                        "type": "text",
                        "start": 0,
                        "end": units(source),
                        "segments": [
                            {
                                "type": "math",
                                "start": 0,
                                "end": units(source),
                                "latex": latex,
                                "mathml": mathml,
                                "display": False,
                            }
                        ],
                    }
                ],
            }
            with self.subTest(latex=latex):
                omml = export._field(value, source, "语料")[0]["segments"][0]["omml"]
                self.assertIsNotNone(omml, "这条公式没有转成原生公式")
                before = "".join(omml.itertext())
                again = export._field(value, source, "语料")[0]["segments"][0]["omml"]
                self.assertEqual("".join(again.itertext()), before, f"{latex} 跑第二遍文字就变了")
                if omml.findall(".//m:func", NS):
                    functions += 1
        self.assertGreaterEqual(functions, 5, "语料里一条 m:func 都没写出来，这条闸门没意义")

    def unconvertible_field(self, latex, body, *, head="化简 ", tail=" 并写出结果。"):
        """一条 Word 明确不支持的公式，夹在题干文字中间。"""
        source = head + "$" + latex + "$" + tail
        mathml = (
            f'<math xmlns="{export.MATH_NS}"><semantics>{body}<annotation encoding="application/x-tex">'
            + __import__("html").escape(latex)
            + "</annotation></semantics></math>"
        )
        cut = units(head) + units("$" + latex + "$")
        segments = [
            {"type": "text", "start": 0, "end": units(head)},
            {"type": "math", "start": units(head), "end": cut, "latex": latex, "mathml": mathml, "display": False},
            {"type": "text", "start": cut, "end": units(source)},
        ]
        return {"source": source, "blocks": [{"type": "text", "start": 0, "end": units(source), "segments": segments}]}

    def fake_picture(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (120, 40), (0, 0, 0, 0)).save(buffer, "PNG")
        return {"bytes": buffer.getvalue(), "size": (120, 40)}

    def post_with_unconvertible_formula(self, *, browsable=True):
        """把「浏览器能不能用」和「公式转不了怎么办」分开测，只 mock 排版那一层。"""
        value = self.unconvertible_field(r"\cancel{x}", '<menclose notation="updiagonalstrike"><mi>x</mi></menclose>')
        pub = self.publication(stem=value["source"], kind="free_response")
        payload = self.payload([pub])
        payload["rendered_fields"][str(pub.id)]["stem"] = value
        return payload, pub

    def test_unconvertible_formula_becomes_a_picture_and_is_named_in_the_warning(self):
        payload, _ = self.post_with_unconvertible_formula()
        with mock.patch.object(export, "_formula_pictures", return_value=[self.fake_picture()]) as renderer:
            result = self.post(payload)
        self.assertEqual(result.status_code, 200, result.content[:300])
        xml = document_xml(result.content)
        text = "".join(xml.itertext())
        # 题干文字一个字不少，公式原样呈现，只是从可编辑公式变成图片。
        self.assertIn("化简", text)
        self.assertIn("并写出结果。", text)
        self.assertNotIn("\\cancel", text)
        self.assertEqual(len(xml.findall(".//a:blip", NS)), 1)
        with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
            self.assertEqual(len([name for name in archive.namelist() if name.startswith("word/media/")]), 1)
        # 警告要能让人在这一卷里直接找到那条公式：第几题、哪个位置。
        warning = unquote(result["X-QB-Layout-Warning"])
        self.assertIn("第 1 题", warning)
        self.assertIn("题干", warning)
        self.assertIn("图片", warning)
        renderer.assert_called_once()

    def test_direct_export_renders_fallback_even_without_a_warning_collector(self):
        payload, _ = self.post_with_unconvertible_formula()
        with mock.patch.object(export, "_formula_pictures", return_value=[self.fake_picture()]) as renderer:
            data, _filename, _mime, _count = export.export(payload)
        self.assertEqual(len(document_xml(data).findall(".//a:blip", NS)), 1)
        renderer.assert_called_once()

    def test_several_unconvertible_formulas_share_one_rendering_pass(self):
        first = self.unconvertible_field(r"\cancel{x}", '<menclose notation="updiagonalstrike"><mi>x</mi></menclose>')
        second = self.unconvertible_field(
            r"\bcancel{y}", '<menclose notation="downdiagonalstrike"><mi>y</mi></menclose>'
        )
        one = self.publication(stem=first["source"])
        two = self.publication(stem=second["source"])
        payload = self.payload([one, two])
        payload["rendered_fields"][str(one.id)]["stem"] = first
        payload["rendered_fields"][str(two.id)]["stem"] = second
        with mock.patch.object(export, "_formula_pictures", return_value=[self.fake_picture()] * 2) as renderer:
            result = self.post(payload)
        self.assertEqual(result.status_code, 200, result.content[:300])
        # 一次启动、一个文档、批量截完，不能每条公式开一次浏览器。
        self.assertEqual(renderer.call_count, 1)
        self.assertEqual(len(renderer.call_args[0][0]), 2)
        self.assertEqual(len(document_xml(result.content).findall(".//a:blip", NS)), 2)

    def test_a_formula_that_drew_nothing_is_refused_instead_of_becoming_a_blank(self):
        """截出来全透明，等于在卷子上挖了个洞 —— 宁可整卷失败也不发。"""

        def shot(pixel, *, draw=False):
            buffer = io.BytesIO()
            image = Image.new("RGBA", (120, 40), pixel)
            if draw:
                from PIL import ImageDraw

                ImageDraw.Draw(image).line((10, 20, 100, 20), fill=(0, 0, 0, 255), width=4)
            image.save(buffer, "PNG")
            return {"data": base64.b64encode(buffer.getvalue()).decode("ascii")}

        box = {"x": 0, "y": 0, "width": 120, "height": 40}
        client = mock.Mock()
        client.call.return_value = shot((0, 0, 0, 0))
        with self.assertRaises(ValueError):
            export._formula_png(client, box)
        client.call.return_value = shot((255, 255, 255, 255), draw=True)
        self.assertEqual(export._formula_png(client, box)["size"], (120, 40))
        # 量不出尺寸的框说明排版没成功，同样不许当成一张空图塞进去。
        for bad in (
            {"x": 0, "y": 0, "width": 0, "height": 40},
            {"x": 0, "y": 0, "width": 9999, "height": 40},
            {"x": 0, "y": 0, "width": 120},
            {"x": 0, "y": 0, "width": "120", "height": 40},
        ):
            with self.assertRaises(ValueError):
                export._formula_png(client, bad)

    def test_formula_capture_retries_a_blank_first_paint(self):
        def shot(draw):
            buffer = io.BytesIO()
            image = Image.new("RGBA", (120, 40), (0, 0, 0, 0))
            if draw:
                from PIL import ImageDraw

                ImageDraw.Draw(image).line((10, 20, 100, 20), fill=(0, 0, 0, 255), width=4)
            image.save(buffer, "PNG")
            return {"data": base64.b64encode(buffer.getvalue()).decode("ascii")}

        client = mock.Mock()
        client.call.side_effect = [shot(False), {}, shot(True)]
        result = export._formula_png(client, {"x": 0, "y": 0, "width": 120, "height": 40})
        self.assertEqual(result["size"], (120, 40))
        self.assertEqual(client.call.call_count, 3)

    def test_without_a_usable_browser_the_whole_download_is_still_refused(self):
        from . import library_pdf

        payload, _ = self.post_with_unconvertible_formula()
        # 本机真的没有浏览器时，不许拿一张空白图片去补那个洞。
        with mock.patch.object(
            library_pdf,
            "_browser_path",
            side_effect=library_pdf.word.ExportError(
                "导出 PDF 需要本机 Microsoft Edge 或 Google Chrome；仍可导出 Word。", 503
            ),
        ):
            result = self.post(payload)
        # 宁可让用户改用 PDF，也不发一份读不懂的卷子。
        self.assertEqual(result.status_code, 400)
        self.assertIn("改用打印", result.json()["error"])
        self.assertNotIn("Content-Disposition", result)

    def test_pdf_export_never_becomes_a_picture(self):
        value = self.unconvertible_field(r"\cancel{x}", '<menclose notation="updiagonalstrike"><mi>x</mi></menclose>')
        pub = self.publication(stem=value["source"])
        payload = self.payload([pub])
        payload["rendered_fields"][str(pub.id)]["stem"] = value
        options = library_drafts._print_options(payload["print_options"], ids=payload["ids"])
        with mock.patch.object(export, "_formula_pictures", side_effect=AssertionError("PDF must not render pictures")):
            # PDF 路径根本不走 OMML，:364 那条老契约继续成立。
            captured, use_ai = export._capture(payload["ids"], payload["rendered_fields"], options, "pdf")
        segment = captured[0]["fields"]["stem"][0]["segments"][1]
        self.assertEqual(segment["type"], "math")
        self.assertIsNone(segment["omml"])
        self.assertEqual(segment["latex"], r"\cancel{x}")

    def test_question_paragraphs_keep_together_and_release_the_final_chain(self):
        pub = self.publication(kind="single_choice", options={"A": "甲", "B": "乙"})
        self.figure(pub, slot="A")
        # Vertical keeps each option in its own body paragraph, which is what
        # this test reads; the two-column grid puts them in table cells.
        response = self.post(
            self.payload(
                [pub],
                print_options={
                    "document": "questions",
                    "student_info": False,
                    "pagination": "keep",
                    "option_layout": "vertical",
                },
            )
        )
        self.assertEqual(response.status_code, 200)
        xml = document_xml(response.content)
        paragraphs = xml.find("w:body", NS).findall("w:p", NS)
        question = [paragraph for paragraph in paragraphs if "离线合成题" in "".join(paragraph.itertext())][0]
        self.assertEqual(question.find("w:pPr/w:keepNext", NS).get(f"{{{NS['w']}}}val"), "1")
        last_option = [paragraph for paragraph in paragraphs if "B. 乙" in "".join(paragraph.itertext())][0]
        self.assertEqual(last_option.find("w:pPr/w:keepNext", NS).get(f"{{{NS['w']}}}val"), "0")

    def test_stale_ai_binding_is_rejected_without_overwriting_original(self):
        features.save({"ai_answer": True})
        pub = self.publication(extras={"ai_answer": {"answer": "旧参考", "fingerprint": "old"}})
        response = self.post(self.payload([pub], print_options={"ai_answers": True}))
        self.assertEqual(response.status_code, 409)
        self.assertIn("不匹配", response.json()["error"])

    def test_pdf_metadata_validates_math_without_invoking_word_converter(self):
        value = math_field(r"\cancel{x}", '<menclose notation="updiagonalstrike"><mi>x</mi></menclose>')
        pub = self.publication(stem=value["source"])
        payload = self.payload([pub], print_options={"document": "questions"})
        payload["rendered_fields"][str(pub.id)]["stem"] = value
        options = library_drafts._print_options(payload["print_options"], ids=payload["ids"])
        with mock.patch.object(
            export, "_convert_math_root", side_effect=AssertionError("Word converter must not run for PDF")
        ):
            captured, use_ai = export._capture(payload["ids"], payload["rendered_fields"], options, "pdf")
        segment = captured[0]["fields"]["stem"][0]["segments"][0]
        self.assertIsNone(segment["omml"])
        self.assertEqual(segment["mathml"], value["blocks"][0]["segments"][0]["mathml"])
        self.assertEqual(segment["latex"], r"\cancel{x}")
        export._recheck(captured, options, use_ai)
        # Word 侧走的是同一条路：这条公式转不了，但它只是降级成图片，不废掉整卷。
        word_captured, _ = export._capture(payload["ids"], payload["rendered_fields"], options, "docx")
        word_segment = word_captured[0]["fields"]["stem"][0]["segments"][0]
        self.assertIsNone(word_segment["omml"])
        self.assertIn("第 1 题", word_segment["fallback"])
        # Skipping OMML compatibility never skips XML, source or annotation checks.
        for mutation in (
            lambda raw: raw.replace("<mi>x</mi>", "<unsupported>x</unsupported>"),
            lambda raw: raw.replace("<mi>x</mi>", '<mi onclick="bad">x</mi>'),
            lambda raw: "<!DOCTYPE math>" + raw,
        ):
            bad = deepcopy(value)
            bad["blocks"][0]["segments"][0]["mathml"] = mutation(bad["blocks"][0]["segments"][0]["mathml"])
            with self.assertRaises(export.ExportError):
                export._field(bad, bad["source"], "合成PDF公式", word_math=False)
        with self.assertRaises(export.ExportError):
            export._field(value, "不同题干", "合成PDF公式", word_math=False)

    def test_compact_splits_source_subquestions_into_breakable_paragraphs(self):
        pub = self.publication(stem="合成题开头\n(1) 第一小问\n(2) 第二小问")
        response = self.post(self.payload([pub], print_options={"document": "questions", "student_info": False}))
        self.assertEqual(response.status_code, 200)
        xml = document_xml(response.content)
        paragraphs = xml.find("w:body", NS).findall("w:p", NS)
        question = [
            paragraph
            for paragraph in paragraphs
            if any(word in "".join(paragraph.itertext()) for word in ("合成题开头", "第一小问", "第二小问"))
        ]
        self.assertEqual(len(question), 3)
        for paragraph in question:
            self.assertEqual(paragraph.find("w:pPr/w:keepLines", NS).get(f"{{{NS['w']}}}val"), "0")
            self.assertEqual(paragraph.find("w:pPr/w:keepNext", NS).get(f"{{{NS['w']}}}val"), "0")
        self.assertEqual(question[-1].find("w:pPr/w:spacing", NS).get(f"{{{NS['w']}}}after"), "160")

    def test_compact_protects_display_formula_and_picture_without_locking_long_prose(self):
        formula = math_field(r"\frac{a}{b}", "<mfrac><mi>a</mi><mi>b</mi></mfrac>", display=True)
        before, after = "长段开头" * 40 + "\n", "\n结束文字"
        source = before + formula["source"] + after
        pub = self.publication(stem=source)
        self.figure(pub)
        payload = self.payload([pub], print_options={"document": "questions"})
        math = deepcopy(formula["blocks"][0]["segments"][0])
        math.update(start=units(before), end=units(before + formula["source"]))
        payload["rendered_fields"][str(pub.id)]["stem"] = {
            "source": source,
            "blocks": [
                {
                    "type": "text",
                    "start": 0,
                    "end": units(source),
                    "segments": [
                        {"type": "text", "start": 0, "end": units(before)},
                        math,
                        {"type": "text", "start": units(before + formula["source"]), "end": units(source)},
                    ],
                }
            ],
        }
        response = self.post(payload)
        self.assertEqual(response.status_code, 200)
        paragraphs = document_xml(response.content).find("w:body", NS).findall("w:p", NS)
        for paragraph in paragraphs:
            if paragraph.find("m:oMath", NS) is not None or paragraph.find(".//w:drawing", NS) is not None:
                self.assertEqual(paragraph.find("w:pPr/w:keepLines", NS).get(f"{{{NS['w']}}}val"), "1")
        prose = next(paragraph for paragraph in paragraphs if "长段开头" in "".join(paragraph.itertext()))
        self.assertEqual(prose.find("w:pPr/w:keepNext", NS).get(f"{{{NS['w']}}}val"), "0")
        self.assertEqual(prose.find("w:pPr/w:keepLines", NS).get(f"{{{NS['w']}}}val"), "0")

    def test_blank_source_lines_add_spacing_without_empty_word_paragraphs(self):
        formula = math_field(r"\frac{a}{b}", "<mfrac><mi>a</mi><mi>b</mi></mfrac>", display=True)
        before, after = "题干开头\n\n  \n(1) 保留答题线________\n\n", "\n\n  \n(2) 保留第二小问\n\n"
        source = before + formula["source"] + after
        pub = self.publication(stem=source)
        payload = self.payload([pub], print_options={"document": "questions", "student_info": False})
        math = deepcopy(formula["blocks"][0]["segments"][0])
        math.update(start=units(before), end=units(before + formula["source"]))
        payload["rendered_fields"][str(pub.id)]["stem"] = {
            "source": source,
            "blocks": [
                {
                    "type": "text",
                    "start": 0,
                    "end": units(source),
                    "segments": [
                        {"type": "text", "start": 0, "end": units(before)},
                        math,
                        {"type": "text", "start": units(before + formula["source"]), "end": units(source)},
                    ],
                }
            ],
        }
        response = self.post(payload)
        self.assertEqual(response.status_code, 200, response.content[:200])
        paragraphs = document_xml(response.content).find("w:body", NS).findall("w:p", NS)
        question = paragraphs[2:]  # title, section heading
        self.assertEqual(len(question), 4)
        self.assertEqual(sum(paragraph.find("m:oMath", NS) is not None for paragraph in question), 1)
        self.assertFalse(
            any(
                not "".join(paragraph.itertext()).strip() and paragraph.find("m:oMath", NS) is None
                for paragraph in question
            )
        )
        text = "".join(document_xml(response.content).itertext())
        for part in ("题干开头", "(1) 保留答题线________", "(2) 保留第二小问"):
            self.assertIn(part, text)

    def test_compact_binds_only_short_caption_to_picture(self):
        short = self.publication(stem="短图注请看下图")
        long = self.publication(stem="多行题干材料" * 12)
        formula = math_field("x" * 80, "<mrow>" + "<mi>x</mi>" * 80 + "</mrow>")
        prefix = "短文字与长公式"
        wide_math = self.publication(stem=prefix + formula["source"])
        self.figure(short)
        self.figure(long)
        self.figure(wide_math)
        payload = self.payload([short, long, wide_math], print_options={"document": "questions"})
        math = deepcopy(formula["blocks"][0]["segments"][0])
        math.update(start=units(prefix), end=units(prefix + formula["source"]))
        payload["rendered_fields"][str(wide_math.id)]["stem"] = {
            "source": wide_math.content["stem"],
            "blocks": [
                {
                    "type": "text",
                    "start": 0,
                    "end": units(wide_math.content["stem"]),
                    "segments": [{"type": "text", "start": 0, "end": units(prefix)}, math],
                }
            ],
        }
        response = self.post(payload)
        self.assertEqual(response.status_code, 200)
        paragraphs = document_xml(response.content).find("w:body", NS).findall("w:p", NS)
        short_paragraph = next(
            paragraph for paragraph in paragraphs if "短图注请看下图" in "".join(paragraph.itertext())
        )
        long_paragraph = next(paragraph for paragraph in paragraphs if "多行题干材料" in "".join(paragraph.itertext()))
        math_paragraph = next(
            paragraph for paragraph in paragraphs if "短文字与长公式" in "".join(paragraph.itertext())
        )
        self.assertEqual(short_paragraph.find("w:pPr/w:keepNext", NS).get(f"{{{NS['w']}}}val"), "1")
        self.assertEqual(long_paragraph.find("w:pPr/w:keepNext", NS).get(f"{{{NS['w']}}}val"), "0")
        self.assertEqual(math_paragraph.find("w:pPr/w:keepNext", NS).get(f"{{{NS['w']}}}val"), "0")
        for paragraph in paragraphs:
            if paragraph.find(".//w:drawing", NS) is not None:
                self.assertEqual(paragraph.find("w:pPr/w:keepLines", NS).get(f"{{{NS['w']}}}val"), "1")

    def test_keep_long_question_falls_back_to_breakable_paragraphs(self):
        pub = self.publication(stem="\n".join(f"第 {index} 段合成材料" * 12 for index in range(60)))
        response = self.post(self.payload([pub], print_options={"document": "questions", "pagination": "keep"}))
        self.assertEqual(response.status_code, 200)
        xml = document_xml(response.content)
        prose = next(
            paragraph
            for paragraph in xml.find("w:body", NS).findall("w:p", NS)
            if "第 1 段合成材料" in "".join(paragraph.itertext())
        )
        self.assertEqual(prose.find("w:pPr/w:keepNext", NS).get(f"{{{NS['w']}}}val"), "0")
        self.assertEqual(prose.find("w:pPr/w:keepLines", NS).get(f"{{{NS['w']}}}val"), "0")

    def test_global_and_per_question_columns_and_font_size_safe_fallback(self):
        first = self.publication(kind="single_choice", options=dict.fromkeys("ABCD", "短选项"))
        second = self.publication(kind="single_choice", options=dict.fromkeys("ABCD", "短选项"))
        # The per-question override still wins, and "four" is honoured: the PDF the
        # teacher checked prints these four-across, so the download has to agree.
        # (Until 2026-11-04 Word downgraded every four-option question to 2x2 by
        # copying a styles.css container query that fitOptions shadows with an
        # inline style — it can never fire in a browser.)
        options = {"document": "questions", "option_layout": "four", "option_overrides": {str(second.id): "vertical"}}
        result = self.post(self.payload([first, second], print_options=options))
        tables = document_xml(result.content).findall(".//w:tbl", NS)
        self.assertEqual([len(table.findall("w:tblGrid/w:gridCol", NS)) for table in tables], [4])
        first.content["options"] = dict.fromkeys("ABCD", "长" * 14)  # wide enough to react to the font size
        first.save(update_fields=["content"])
        columns = []
        for size in (12, 16):
            result = self.post(
                self.payload(
                    [first], print_options={"document": "questions", "option_layout": "four", "font_size": size}
                )
            )
            table = document_xml(result.content).find(".//w:tbl", NS)
            columns.append(len(table.findall("w:tblGrid/w:gridCol", NS)) if table is not None else 0)
            self.assertIn(f'w:val="{size * 2}"'.encode(), etree.tostring(document_xml(result.content)))
        self.assertEqual(columns, [2, 0], "a bigger font must still fall back: two columns, then one per line")

    def test_per_question_answer_space_override_matches_the_global_setting_it_copies(self):
        first = self.publication(kind="free_response", stem="第一题专用题干标记")
        second = self.publication(kind="free_response", stem="第二题专用题干标记")

        def writing_space(stem, print_options):
            """The `w:after` of the blank paragraph the writer adds after that question."""
            response = self.post(self.payload([first, second], print_options=print_options))
            self.assertEqual(response.status_code, 200)
            paragraphs = document_xml(response.content).find("w:body", NS).findall("w:p", NS)
            index = next(i for i, p in enumerate(paragraphs) if stem in "".join(p.itertext()))
            for paragraph in paragraphs[index + 1 :]:
                if "".join(paragraph.itertext()).strip():
                    break
                spacing = paragraph.find("w:pPr/w:spacing", NS)
                if spacing is not None and spacing.get(f"{{{NS['w']}}}after"):
                    return spacing.get(f"{{{NS['w']}}}after")
            return None

        none = {"document": "questions", "answer_space": "none"}
        large = {"document": "questions", "answer_space": "large"}
        only_second = {**none, "answer_space_overrides": {str(second.id): "large"}}

        # The override has to print exactly what picking that value globally would.
        self.assertEqual(writing_space("第二题专用题干标记", only_second), writing_space("第二题专用题干标记", large))
        # And it must not drag the other question along with it.
        self.assertEqual(writing_space("第一题专用题干标记", only_second), writing_space("第一题专用题干标记", none))
        self.assertNotEqual(
            writing_space("第一题专用题干标记", only_second), writing_space("第一题专用题干标记", large)
        )
        # Without a space there is nothing to inherit, so the override must add one.
        self.assertIsNone(writing_space("第二题专用题干标记", none))
        self.assertIsNotNone(writing_space("第二题专用题干标记", only_second))

    def test_five_options_and_wide_forced_four_never_drop_or_shrink_content(self):
        pub = self.publication(kind="single_choice", options=dict.fromkeys("ABCDE", "短选项"))
        result = self.post(self.payload([pub], print_options={"document": "questions", "option_layout": "four"}))
        xml = document_xml(result.content)
        table = xml.find(".//w:tbl", NS)
        self.assertEqual(len(table.findall("w:tblGrid/w:gridCol", NS)), 2)
        self.assertIn("E. 短选项", "".join(table.itertext()))
        pub.content["options"] = dict.fromkeys("ABCD", "长选项" * 30)
        pub.save(update_fields=["content"])
        result = self.post(
            self.payload([pub], print_options={"document": "questions", "option_layout": "four", "font_size": 16})
        )
        xml = document_xml(result.content)
        self.assertIsNone(xml.find(".//w:tbl", NS))
        for key in "ABCD":
            self.assertIn(key + ". " + "长选项" * 30, "".join(xml.itertext()))

    def test_a_non_choice_question_warns_before_its_own_letters_reach_the_paper(self):
        # 数学九上第 1 题：书上印的是 (1)(2)(3)(4) 和物体下面的括号，12 张图却被
        # 分到了 A~D 槽。写出选项字母那段不看题型，卷面就凭空多出 A. B. C. D.，
        # 序号和括号两样都没了。
        pub = self.publication(stem="根据下列主视图和俯视图，将视图的序号填在相应物体下面的括号里：")
        self.figure(pub, slot="A")
        result = self.post(self.payload([pub]))
        self.assertEqual(result.status_code, 200, result.content[:300])
        self.assertIn("A.", "".join(document_xml(result.content).itertext()))
        warning = unquote(result.headers["X-QB-Layout-Warning"]) if "X-QB-Layout-Warning" in result.headers else ""
        self.assertIn("第 1 题", warning, "非选择题印出 A. B. C. 时必须有一条能定位到它的提示")
        self.assertIn("不是选择题", warning)
        self.assertIn("(1)(2)(3)(4)", warning)

    def test_a_choice_question_keeps_its_letters_without_a_warning(self):
        pub = self.publication(stem="下列说法正确的是", kind="single_choice")
        self.figure(pub, slot="A")
        result = self.post(self.payload([pub]))
        self.assertEqual(result.status_code, 200, result.content[:300])
        self.assertIn("A.", "".join(document_xml(result.content).itertext()))
        self.assertFalse(result.has_header("X-QB-Layout-Warning"))

    def test_a_question_whose_pictures_all_sit_on_the_stem_never_gets_option_letters(self):
        pub = self.publication(stem="画出这个几何体的三视图")
        self.figure(pub, slot="stem")
        result = self.post(self.payload([pub]))
        self.assertEqual(result.status_code, 200, result.content[:300])
        self.assertNotIn("A.", "".join(document_xml(result.content).itertext()))
        self.assertFalse(result.has_header("X-QB-Layout-Warning"))

    def test_selected_page_breaks_bind_section_heading_and_do_not_repeat_in_answers(self):
        first = self.publication(kind="single_choice", answer="甲")
        second = self.publication(kind="single_choice", answer="乙")
        third = self.publication(kind="free_response", answer="丙")
        options = {"question_breaks": [str(first.id), str(second.id), str(third.id)], "document": "combined"}
        response = self.post(self.payload([first, second, third], print_options=options))
        self.assertEqual(response.status_code, 200)
        paragraphs = document_xml(response.content).find("w:body", NS).findall("w:p", NS)
        breaks = [paragraph for paragraph in paragraphs if paragraph.find("w:pPr/w:pageBreakBefore", NS) is not None]
        self.assertEqual(len(breaks), 2)
        self.assertTrue("".join(breaks[0].itertext()).startswith("2."))
        self.assertIn("解答题", "".join(breaks[1].itertext()))
        answer_index = next(
            index for index, paragraph in enumerate(paragraphs) if "参考答案与解析" in "".join(paragraph.itertext())
        )
        self.assertFalse(
            any(paragraph.find("w:pPr/w:pageBreakBefore", NS) is not None for paragraph in paragraphs[answer_index:])
        )

    def test_layout_hints_for_unselected_ids_are_rejected_by_export(self):
        pub = self.publication()
        for hints in ({"option_overrides": {str(uuid.uuid4()): "four"}}, {"question_breaks": [str(uuid.uuid4())]}):
            response = self.post(self.payload([pub], print_options=hints))
            self.assertEqual(response.status_code, 400)
            self.assertIn("当前选中", response.json()["error"])

    def test_mathml_annotation_mismatch_external_entities_and_unsupported_nodes_fail(self):
        value = math_field("x", "<mi>x</mi>")
        for change in (
            lambda raw: raw.replace(">x</annotation>", ">z</annotation>"),
            lambda raw: '<!DOCTYPE math [<!ENTITY x SYSTEM "file:///private">]>' + raw,
            lambda raw: raw.replace("<mi>x</mi>", "<unsupported>x</unsupported>"),
            lambda raw: raw.replace("<mi>x</mi>", '<mi>x</mi><mi href="https://invalid">y</mi>'),
        ):
            bad = deepcopy(value)
            segment = bad["blocks"][0]["segments"][0]
            segment["mathml"] = change(segment["mathml"])
            with self.assertRaises(export.ExportError):
                export._field(bad, bad["source"], "合成公式")

    def test_unconverted_latex_is_never_exported_as_success(self):
        pub = self.publication(stem=r"求 $\frac{1}{x}$")
        result = self.post(self.payload([pub]))
        self.assertEqual(result.status_code, 400)
        self.assertIn("LaTeX", result.json()["error"])

    def test_markdown_table_is_native_and_client_cannot_change_or_hide_cells(self):
        raw = "| 项 | 值 |\n| --- | --- |\n| 甲 | 2 |"
        pub = self.publication(stem=raw)
        rows = export._table_source(raw, "合成表格")
        for row in rows:
            for cell in row:
                cell["segments"] = field(cell["source"])["blocks"][0]["segments"] if cell["source"] else []
        value = {"source": raw, "blocks": [{"type": "table", "start": 0, "end": units(raw), "rows": rows}]}
        payload = self.payload([pub])
        payload["rendered_fields"][str(pub.id)]["stem"] = value
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        xml = document_xml(result.content)
        self.assertEqual(len(xml.findall(".//w:tbl", NS)), 1)
        self.assertIn("甲", "".join(xml.itertext()))
        rows[1][1]["source"] = "3"
        self.assertEqual(self.post(payload).status_code, 400)
        rows[1].pop()
        self.assertEqual(self.post(payload).status_code, 400)

    def table_export(self, raw):
        """把一段 Markdown 表格走完真导出，返回 docx 的 XML。"""
        pub = self.publication(stem=raw)
        rows = export._table_source(raw, "合成表格")
        for row in rows:
            for cell in row:
                cell["segments"] = field(cell["source"])["blocks"][0]["segments"] if cell["source"] else []
        payload = self.payload([pub])
        payload["rendered_fields"][str(pub.id)]["stem"] = {
            "source": raw,
            "blocks": [{"type": "table", "start": 0, "end": units(raw), "rows": rows}],
        }
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        return document_xml(result.content)

    @staticmethod
    def column_widths_mm(xml):
        return [int(node.get(f"{{{NS['w']}}}w")) * 25.4 / 1440 for node in xml.findall(".//w:tblGrid/w:gridCol", NS)]

    def test_a_long_header_column_gets_the_width_its_text_needs(self):
        """宽表里每一列都等分的话，长表头会被挤成五行——真库 27 个表格里 11 个这样。

        九上第 1 题（#1282）是 13 列，「零售收入/万元」七个字要 30mm，
        等分只能给到 13.7mm；同一份内容在 PDF 路线里是一行。
        """
        header = "| 零售收入/万元 | " + " | ".join(str(day) for day in range(12, 24)) + " |"
        row = "| " + " | ".join("40" for _ in range(12)) + " |"
        raw = f"{header}\n| --- | {' | '.join(['---'] * 12)} |\n{row}"
        widths = self.column_widths_mm(self.table_export(raw))

        self.assertEqual(len(widths), 13)
        self.assertGreater(
            widths[0],
            max(widths[1:]) * 1.8,
            f"表头列只有 {widths[0]:.1f}mm，数字列 {max(widths[1:]):.1f}mm，没按内容分",
        )
        # 「12」两个数字是 5.02mm，Word 标称的两侧内边距是 3.8mm。
        # 正好卡在 8.82mm 上就会断成两行——实测就是，所以数字列必须留出余量。
        self.assertGreaterEqual(
            max(widths[1:]), 2 * 0.6 * 12 * 25.4 / 72 + 3.8 + 0.8, f"数字列只有 {max(widths[1:]):.2f}mm，放不下两个数字"
        )

    def test_a_table_wider_than_the_page_is_scaled_back_inside_it(self):
        """内容再宽也不能超出 178mm 的版心，超了就整体缩回去。"""
        header = "| 项目 | " + " | ".join(f"第{i}列" for i in range(1, 20)) + " |"
        row = "| 甲 | " + " | ".join("乙乙" for _ in range(19)) + " |"
        raw = f"{header}\n| --- | {' | '.join(['---'] * 19)} |\n{row}"
        widths = self.column_widths_mm(self.table_export(raw))

        self.assertLessEqual(sum(widths), 178.5)

    def test_a_narrow_table_is_left_at_its_own_width_and_centred(self):
        """内容窄的表不硬撑满版心——预览里那张表是 `margin: 0 auto`，居中收着。"""
        raw = "| 甲 | 乙 |\n| --- | --- |\n| 1 | 2 |"
        xml = self.table_export(raw)
        widths = self.column_widths_mm(xml)

        self.assertLess(sum(widths), 178.0)
        self.assertEqual(xml.find(".//w:tbl/w:tblPr/w:jc", NS).get(f"{{{NS['w']}}}val"), "center")

    def test_an_over_wide_table_keeps_the_columns_that_already_fit(self):
        """版心放不下时，被挤的只该是本来就超宽的那几列。

        九上「根据图象，填写下表」那张五列表（性质 二次函数 / 开口方向 / 对称轴 /
        顶点坐标 / 取哪些值时…）需求合计 195.2mm，超版心 17.2mm。按比例全体缩到
        0.912，「开口方向」本该 21.93mm 只剩 20.00mm，扣掉 Word 单元格左右各
        1.9mm 只余 16.2mm，而四个字实测 16.99mm——差 0.79mm，表头就印成
        「开口方 / 向」两行。需求本来放得下的三列全被陪着缩窄了。
        """
        raw = (
            "| 性质 二次函数 | 开口方向 | 对称轴 | 顶点坐标 | 取哪些值时，x的值随y值的增大而增大 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| y = 1/2 * x^2 |  |  |  |  |\n"
            "| y = 1/2 * (x + 2)^2 |  |  |  |  |"
        )
        widths = self.column_widths_mm(self.table_export(raw))

        # 「开口方向」四个字：12pt 汉字一个 1em = 4.23mm，加上留白。
        # docx 的列宽存在 twip 里（1 twip = 0.0176mm），比下来会少 0.008mm。
        tolerance = 25.4 / 1440
        self.assertGreaterEqual(
            widths[1],
            4 * 12 * 25.4 / 72 + export.CELL_PADDING_MM - tolerance,
            f"「开口方向」列只有 {widths[1]:.2f}mm，表头会被顶成「开口方 / 向」两行",
        )
        self.assertGreaterEqual(
            widths[3], 4 * 12 * 25.4 / 72 + export.CELL_PADDING_MM - tolerance, f"「顶点坐标」列只有 {widths[3]:.2f}mm"
        )
        self.assertLessEqual(sum(widths), 178.5)

    def test_squeezing_a_wide_table_wraps_fewer_cells_than_scaling_every_column(self):
        """水位线分法换掉按比例缩放，为的是折行的格子更少，不是每列都更宽。

        水位线必须**刚好凑满版心**：``sum(min(need_i, λ)) == 178``，且每一列拿到的
        就是 ``min(need_i, λ)``。这个方程的解唯一，所以「凑满」就等于「水位最低、
        能让多少列保持完整需求就让多少列保持」。等分和按比例都不满足——等分留下
        45mm 没用上，按比例则让所有列都折行。

        不写死毫米数：拿九上那两张真的超宽表（五列需求合计 195.2mm、十三列
        244.2mm）当输入。
        """
        for need in (
            [48.18, 21.93, 17.70, 21.93, 85.43],
            [30.40, 27.86, 10.08, 27.86, 10.08, 10.08, 27.86, 25.32, 8.00, 8.00, 25.32, 8.00, 25.32],
            [60.0, 60.0, 60.0, 12.0],
            [200.0],
            [90.0, 90.0, 90.0],
        ):
            budget = 178.0
            fair = export._fair_widths(need, budget)
            squeezed = [got for want, got in zip(need, fair) if got < want - 1e-9]

            self.assertTrue(squeezed, f"{need} 本该挤掉几列，却一列没挤")
            level = min(squeezed)
            self.assertAlmostEqual(
                sum(min(want, level) for want in need), budget, places=6, msg=f"{need} 水位线 {level:.3f}mm 没凑满版心"
            )
            for index, (want, got) in enumerate(zip(need, fair)):
                self.assertAlmostEqual(
                    got,
                    min(want, level),
                    places=6,
                    msg=f"第 {index} 列拿到 {got:.3f}mm，水位线却是 {min(want, level):.3f}mm",
                )

    def test_a_single_wide_column_is_capped_at_the_text_width(self):
        """一列就超出版心时，也不能让它把纸撑出去。"""
        fair = export._fair_widths([300.0], 178.0)

        self.assertEqual([round(value, 3) for value in fair], [178.0])

    def test_html_merged_table_has_native_span_structure(self):
        raw = '<table><tr><th rowspan="2">甲</th><th>乙</th></tr><tr><td>&amp; 丙</td></tr></table>'
        pub = self.publication(stem=raw)
        rows = export._table_source(raw, "合成表格")
        for row in rows:
            for cell in row:
                cell["segments"] = field(cell["source"])["blocks"][0]["segments"]
        payload = self.payload([pub])
        payload["rendered_fields"][str(pub.id)]["stem"] = {
            "source": raw,
            "blocks": [{"type": "table", "start": 0, "end": units(raw), "rows": rows}],
        }
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        xml = document_xml(result.content)
        self.assertTrue(xml.findall(".//w:vMerge", NS))
        self.assertIn("& 丙", "".join(xml.itertext()))

    def test_table_span_out_of_bounds_and_embedded_image_are_rejected(self):
        for raw in (
            '<table><tr><td rowspan="3">甲</td></tr></table>',
            '<table><tr><td><img src="https://invalid">甲</td></tr></table>',
        ):
            pub = self.publication(stem=raw)
            try:
                rows = export._table_source(raw, "合成表格")
            except export.ExportError:
                continue
            for row in rows:
                for cell in row:
                    cell["segments"] = field(cell["source"])["blocks"][0]["segments"]
            payload = self.payload([pub])
            payload["rendered_fields"][str(pub.id)]["stem"] = {
                "source": raw,
                "blocks": [{"type": "table", "start": 0, "end": units(raw), "rows": rows}],
            }
            self.assertEqual(self.post(payload).status_code, 400)

    def test_missing_corrupt_unknown_slot_and_unsafe_image_path_abort(self):
        pub = self.publication()
        target = self.figure(pub)
        for mutation in (
            lambda: target.unlink(),
            lambda: target.write_bytes(b"bad png"),
            lambda: pub.content["figures"][0].update(slot="unknown"),
            lambda: pub.content["figures"][0].update(file="../private.png"),
        ):
            self.figure(pub)
            mutation()
            pub.save(update_fields=["content"])
            response = self.post(self.payload([pub]))
            self.assertEqual(response.status_code, 409)
            self.assertIn("配图 1", response.json()["error"])

    def test_image_only_option_keeps_its_letter(self):
        pub = self.publication(kind="single_choice", options={"A": "", "B": "文字"})
        self.figure(pub, slot="A")
        response = self.post(self.payload([pub]))
        self.assertEqual(response.status_code, 200)
        xml = document_xml(response.content)
        self.assertIn("A.", "".join(xml.itertext()))
        self.assertEqual(len(xml.findall(".//a:blip", NS)), 1)

    def test_four_short_options_auto_uses_four_columns_and_complex_options_keep_vertical(self):
        # Four short options: one row of four. This is what the preview and the PDF
        # have always shown — styles.css turns four columns into two only under a
        # 680px container query, and exam-layout.js fitOptions writes an inline
        # grid-template-columns on every option list, which outranks it.
        pub = self.publication(kind="single_choice", options=dict.fromkeys("ABCD", "离线选项"))
        result = self.post(self.payload([pub], print_options={"document": "questions"}))
        xml = document_xml(result.content)
        table = xml.find(".//w:tbl", NS)
        self.assertIsNotNone(table)
        self.assertEqual(len(table.findall("w:tr", NS)), 1)
        self.assertEqual(len(table.findall("w:tblGrid/w:gridCol", NS)), 4)
        self.assertEqual(table.find("w:tblPr/w:tblBorders/w:top", NS).get(f"{{{NS['w']}}}val"), "nil")
        for paragraph in table.findall("w:tr", NS)[-1].findall(".//w:p", NS):
            self.assertEqual(paragraph.find("w:pPr/w:keepNext", NS).get(f"{{{NS['w']}}}val"), "0")
        # Wide, multi-line or diagram options still fall back to one per line.
        for mutation in (
            lambda: pub.content["options"].update(A="长" * 37),
            lambda: pub.content["options"].update(A="第一行\n第二行"),
        ):
            pub.content["options"]["A"] = "离线选项"
            mutation()
            pub.save(update_fields=["content"])
            response = self.post(self.payload([pub], print_options={"document": "questions"}))
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(document_xml(response.content).find(".//w:tbl", NS))

    def test_four_option_figures_use_the_same_two_columns_as_the_preview(self):
        # Diagrams on the choices are the case that reads worst when it drifts:
        # the preview shows them 2x2 and the download used to show four lines.
        pub = self.publication(kind="single_choice", options=dict.fromkeys("ABCD", ""))
        folder = self.root / "library" / str(pub.id)
        folder.mkdir(parents=True, exist_ok=True)
        pub.content["figures"] = []
        for index, key in enumerate("ABCD", 1):
            target = folder / f"figure-{index}.png"
            Image.new("RGB", (240, 120), "red").save(target)
            pub.content["figures"].append({"slot": key, "file": target.name, "page_idx": 0, "bbox": [0, 0, 100, 100]})
        pub.content_hash = library.content_hash(pub.content)
        pub.save(update_fields=["content", "content_hash"])
        response = self.post(self.payload([pub], print_options={"document": "questions"}))
        self.assertEqual(response.status_code, 200, response.content[:200])
        tree = document_xml(response.content)
        table = tree.find(".//w:tbl", NS)
        self.assertIsNotNone(table, "four option diagrams must lay out as 2x2, not one per line")
        self.assertEqual(len(table.findall("w:tblGrid/w:gridCol", NS)), 2)
        self.assertEqual(len(table.findall("w:tr", NS)), 2)
        self.assertEqual(len(table.findall(".//a:blip", NS)), 4)
        for letter in "ABCD":
            self.assertIn(letter + ".", "".join(tree.itertext()))
        # A diagram too wide for two columns still goes one per line. Asking for
        # two columns explicitly is what makes the download say it could not.
        Image.new("RGB", (900, 300), "red").save(folder / "figure-1.png")
        response = self.post(self.payload([pub], print_options={"document": "questions", "option_layout": "two"}))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(document_xml(response.content).find(".//w:tbl", NS))
        self.assertIn("X-QB-Layout-Warning", response)

    def test_explicit_picture_columns_retain_all_labels_images_and_natural_width(self):
        pub = self.publication(kind="single_choice", options=dict.fromkeys("ABCD", ""))
        folder = self.root / "library" / str(pub.id)
        folder.mkdir(parents=True)
        pub.content["figures"] = []
        for index, key in enumerate("ABCD", 1):
            target = folder / (f"figure-{index}.png")
            Image.new("RGB", (200, 90), "red").save(target)
            pub.content["figures"].append({"slot": key, "file": target.name, "page_idx": 0, "bbox": [0, 0, 100, 100]})
        pub.content_hash = library.content_hash(pub.content)
        pub.save(update_fields=["content", "content_hash"])
        for mode, columns in (("four", 4), ("two", 2), ("vertical", 0), ("auto", 4)):
            # A 200px diagram fits a 178mm/4 column, so "four" and "auto" keep four
            # columns and every label and image survives. Only the explicit "two"
            # and "vertical" reduce.
            with self.subTest(mode=mode):
                response = self.post(
                    self.payload([pub], print_options={"document": "questions", "option_layout": mode})
                )
                self.assertEqual(response.status_code, 200, response.content[:100])
                tree = document_xml(response.content)
                table = tree.find(".//w:tbl", NS)
                self.assertEqual(len(table.findall("w:tblGrid/w:gridCol", NS)) if table is not None else 0, columns)
                self.assertEqual(len(tree.findall(".//a:blip", NS)), 4)
                for letter in "ABCD":
                    self.assertIn(letter + ".", "".join(tree.itertext()))
                extents = tree.xpath(
                    "//*[local-name()='extent' and namespace-uri()='http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing']/@cx"
                )
                self.assertTrue(all(abs(int(width) / 36000 - 200 * 25.4 / 150) < 0.01 for width in extents))

        # A wide image downgrades the requested grid and is never shrunk just
        # to preserve the number of columns. The transport explains the change.
        Image.new("RGB", (600, 90), "red").save(folder / "figure-1.png")
        response = self.post(self.payload([pub], print_options={"document": "questions", "option_layout": "four"}))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(document_xml(response.content).find(".//w:tbl", NS))
        self.assertIn("X-QB-Layout-Warning", response)

    def test_stem_figures_wrap_onto_the_same_rows_as_the_preview(self):
        """The download must read like the preview, rows and sizes both.

        The print preview does not size figures with CSS at all. Once every
        picture has decoded, ``exam-layout.js`` writes its width inline::

            width = px * min(96 / 150, 178mm / px_width, 210mm / px_height)

        so a figure lands at Word's natural 150 dpi size, never wider than the
        text area and never taller than one A4 page. The stylesheet's
        ``max-width: 320px`` / ``max-height: 220px`` are overridden inside the
        print flow by library.css, so reading them measures a view the teacher
        never looks at before exporting — which is how the download ended up
        bigger than the preview it was checked against.

        ``_figure_paper_size`` is that formula, and ``_figure_row_lines`` is
        the plain greedy wrap of ``.qb-figures``: fill a line until the next
        picture does not fit, then start another. The preview never scales a
        row to keep it on one line, so neither does the download. The line
        lengths are kept rather than collapsed to one count, because the
        preview centres every line on its own.

        ``width_mm`` is the picture size on paper. The numbers are literals on
        purpose — deriving them from _figure_paper_size would only prove the
        function agrees with itself.
        """
        for label, sizes, lines, width_mm in (
            ("4 x 240px fills one row", [(240, 120)] * 4, [4], 40.6),
            ("3 x 240px fills one row", [(240, 120)] * 3, [3], 40.6),
            ("6 x 240px wraps four then two", [(240, 120)] * 6, [4, 2], 40.6),
            ("4 x 320px wraps three then one", [(320, 200)] * 4, [3, 1], 54.2),
            ("4 x 120px fills one row", [(120, 90)] * 4, [4], 20.3),
            ("5 x 300px wraps three then two", [(300, 200)] * 5, [3, 2], 50.8),
            # Sits right on the wrap boundary: 3 x 56.73mm plus two 3.70mm gaps
            # is 177.59mm, inside the 178mm text area by 0.4mm. Read the gap one
            # point off and this row no longer fits, which is what keeps the
            # constant honest.
            ("3 x 335px fills one row by 0.4mm", [(335, 200)] * 3, [3], 56.7),
            # 必修第一册 1-1: four diagrams, two to a line at 150 dpi.
            ("textbook 4 diagrams", [(340, 354), (347, 360), (336, 360), (326, 357)], [2, 2], [57.6, 58.8, 56.9, 55.2]),
        ):
            with self.subTest(label):
                count = len(sizes)
                pub = self.publication(kind="free_response")
                folder = self.root / "library" / str(pub.id)
                folder.mkdir(parents=True, exist_ok=True)
                pub.content["figures"] = []
                for index, size in enumerate(sizes, start=1):
                    target = folder / f"figure-{index}.png"
                    Image.new("RGB", size, "red").save(target)
                    pub.content["figures"].append(
                        {"slot": "stem", "file": target.name, "page_idx": 0, "bbox": [0, 0, 100, 100]}
                    )
                pub.content_hash = library.content_hash(pub.content)
                pub.save(update_fields=["content", "content_hash"])

                response = self.post(self.payload([pub], print_options={"document": "questions"}))
                self.assertEqual(response.status_code, 200, response.content[:200])
                tree = document_xml(response.content)
                # One borderless table per preview line, each a single row. A
                # single Word grid cannot do this: it would put a lone trailing
                # diagram in the first column of a table centred on the widest
                # line, hard against the left of the text area.
                tables = tree.findall(".//w:tbl", NS)
                self.assertEqual(
                    [len(table.findall("w:tblGrid/w:gridCol", NS)) for table in tables],
                    lines,
                    f"{label}: one table per preview line",
                )
                for table in tables:
                    self.assertEqual(
                        len(table.findall("w:tr", NS)), 1, f"{label}: a preview line is one row, not a grid"
                    )
                # Word folds two w:tbl elements that sit back to back into ONE
                # table, which would drag the second line's columns back to the
                # first line's width and undo the per-line centring. The XML
                # still shows two tables either way, so nothing above would notice
                # a missing separator — this is the assertion that does.
                wanted_tables = {id(table) for table in tables}
                seated = [
                    index
                    for index, child in enumerate(tree.find("w:body", NS))
                    if child.tag == f"{{{NS['w']}}}tbl" and id(child) in wanted_tables
                ]
                self.assertEqual(len(seated), len(tables), f"{label}: figure tables in the body")
                for position in range(len(seated) - 1):
                    self.assertGreater(
                        seated[position + 1] - seated[position],
                        1,
                        f"{label}: two figure lines sit back to back, so Word merges them "
                        f"into one table and the second line loses its own width",
                    )
                self.assertEqual(len(tree.findall(".//a:blip", NS)), count)
                gap_mm = export.PREVIEW_FIGURE_GAP_PX * 25.4 / 96
                extents = [
                    int(value) / 36000
                    for value in tree.xpath(
                        "//*[local-name()='extent' and namespace-uri()="
                        "'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing']/@cx"
                    )
                ]
                wanted = width_mm if isinstance(width_mm, list) else [width_mm] * count
                self.assertEqual([round(value, 1) for value in extents], wanted, f"{label}: mm on paper")
                cursor = 0
                for number, columns in enumerate(lines, start=1):
                    table = tables[number - 1]
                    row = wanted[cursor : cursor + columns]
                    cursor += columns
                    # Borderless, like the preview's plain figure row.
                    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                        node = table.find(f"w:tblPr/w:tblBorders/w:{side}", NS)
                        self.assertEqual(node.get(f"{{{NS['w']}}}val"), "nil", f"{label} line {number}: {side} border")
                    # The preview centres every line on its own; a table left
                    # aligned on an A4 text area is the difference the teacher sees.
                    justification = table.find("w:tblPr/w:jc", NS)
                    self.assertIsNotNone(justification, f"{label} line {number}: has to be centred")
                    self.assertEqual(justification.get(f"{{{NS['w']}}}val"), "center")
                    # Centring the table is only half of it. Each column is a gap
                    # wider than its picture, and the picture has to sit in the
                    # middle of that column — a left-aligned cell paragraph puts
                    # it hard against the neighbour and drags the row left by half
                    # a gap, about 1.9mm, which is visible on the page.
                    for cell in table.findall(".//w:tc", NS):
                        inner = cell.find("w:p/w:pPr/w:jc", NS)
                        self.assertIsNotNone(inner, f"{label} line {number}: a cell has to centre its picture")
                        self.assertEqual(
                            inner.get(f"{{{NS['w']}}}val"),
                            "center",
                            f"{label} line {number}: a cell paragraph is not centred",
                        )
                    # Word table columns touch, so each column carries the gap the
                    # preview shows — including the last one, which is what keeps
                    # the same space before the first diagram as after the last.
                    column_mm = [
                        int(column.get(f"{{{NS['w']}}}w")) * 25.4 / 1440
                        for column in table.findall("w:tblGrid/w:gridCol", NS)
                    ]
                    for position in range(columns):
                        slack = column_mm[position] - row[position]
                        self.assertGreater(
                            slack,
                            1.5,
                            f"{label} line {number}: column {position + 1} "
                            f"has no gap ({column_mm[position]} vs {row[position]})",
                        )
                    # The pictures and the gaps between them must fit the text
                    # area. The columns add one empty gap at each end on top of
                    # that; it is the space that centres the row and may spill
                    # into the A4 margins, which is far better than shrinking
                    # every diagram by a percent and putting it out of step.
                    self.assertLessEqual(
                        sum(column_mm) - gap_mm, 178, f"{label} line {number}: the row runs off the page"
                    )

        # Two pictures too wide to share a row get one paragraph each — the
        # preview wraps them too, and neither side may shrink them to force a
        # grid. Each still lands at the same 150 dpi natural size, capped to
        # the text area, so a lone figure is unaffected by the row rules.
        for label, sizes, expected in (
            ("必修第一册 2-2 wide strips", ((1484, 144), (1476, 147)), [178.0, 178.0]),
            ("two 600x100 strips", ((600, 100), (600, 100)), [101.6, 101.6]),
            # The other cap: too tall for one A4 page. 210mm of a 100x2000 crop
            # is 10.5mm wide — not the 16.9mm its own pixels would ask for.
            ("one very tall strip", ((100, 2000),), [10.5]),
        ):
            with self.subTest(label):
                pub = self.publication(kind="free_response")
                folder = self.root / "library" / str(pub.id)
                folder.mkdir(parents=True, exist_ok=True)
                pub.content["figures"] = []
                for index, size in enumerate(sizes, start=1):
                    target = folder / f"figure-{index}.png"
                    Image.new("RGB", size, "red").save(target)
                    pub.content["figures"].append(
                        {"slot": "stem", "file": target.name, "page_idx": 0, "bbox": [0, 0, 100, 100]}
                    )
                pub.content_hash = library.content_hash(pub.content)
                pub.save(update_fields=["content", "content_hash"])
                response = self.post(self.payload([pub], print_options={"document": "questions"}))
                self.assertEqual(response.status_code, 200)
                tree = document_xml(response.content)
                self.assertIsNone(tree.find(".//w:tbl", NS), f"{label}: one column must not build a grid")
                self.assertEqual(len(tree.findall(".//a:blip", NS)), len(sizes))
                extents = [
                    round(int(value) / 36000, 1)
                    for value in tree.xpath(
                        "//*[local-name()='extent' and namespace-uri()="
                        "'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing']/@cx"
                    )
                ]
                self.assertEqual(extents, expected, f"{label}: mm on paper")

    def test_a_teacher_can_resize_realign_and_reline_their_figures(self):
        """改过的预览就是最终的卷面，Word 只能照搬。

        老师在这一份卷子里动过的三样东西：每张图多宽、整行靠左还是靠右、点名让
        第几张图换行。预览和 PDF 共用 ``exam-layout.js``，只有 Word 是这份代码，
        所以最容易分家 —— 分家的表现是「预览看着对，印出来不是那样」，而老师
        恰恰只在预览里动手。

        三个 240x120px 的图在 150dpi 下是 40.6mm，天然挤在一行（3×40.6 + 2×3.7
        = 129.3mm）。下面每一项都先说清楚它为什么必须和预览一致，再看 Word。
        """
        sizes = [(240, 120)] * 4
        wanted = [40.6] * 3

        def build(count):
            pub = self.publication(kind="free_response")
            folder = self.root / "library" / str(pub.id)
            folder.mkdir(parents=True, exist_ok=True)
            pub.content["figures"] = []
            for index, size in enumerate(sizes[:count], start=1):
                target = folder / f"figure-{index}.png"
                Image.new("RGB", size, "red").save(target)
                pub.content["figures"].append(
                    {"slot": "stem", "file": target.name, "page_idx": 0, "bbox": [0, 0, 100, 100]}
                )
            pub.content_hash = library.content_hash(pub.content)
            pub.save(update_fields=["content", "content_hash"])
            return pub

        def widths_mm(tree):
            return [
                round(int(value) / 36000, 1)
                for value in tree.xpath(
                    "//*[local-name()='extent' and namespace-uri()="
                    "'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing']/@cx"
                )
            ]

        def heights_mm(tree):
            return [
                round(int(value) / 36000, 1)
                for value in tree.xpath(
                    "//*[local-name()='extent' and namespace-uri()="
                    "'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing']/@cy"
                )
            ]

        def line_shapes(tree):
            return [len(table.findall("w:tblGrid/w:gridCol", NS)) for table in tree.findall(".//w:tbl", NS)]

        def column_mm(table):
            return [
                round(int(column.get(f"{{{NS['w']}}}w")) * 25.4 / 1440, 2)
                for column in table.findall("w:tblGrid/w:gridCol", NS)
            ]

        # 不动任何东西时，跟没有这个功能之前一模一样。
        pub = build(3)
        response = self.post(self.payload([pub], print_options={"document": "questions", "figure_overrides": {}}))
        self.assertEqual(response.status_code, 200, response.content[:200])
        tree = document_xml(response.content)
        self.assertEqual(line_shapes(tree), [3], "没设置时三张图仍然在一行")
        self.assertEqual(widths_mm(tree), wanted)

        # 放大一张：预览里 flex 塞不下就换行，Word 也得换行，而且换在哪一行都要一样。
        pub = build(3)
        options = {"document": "questions", "figure_overrides": {str(pub.id): {"width": {"0": 100}}}}
        tree = document_xml(self.post(self.payload([pub], print_options=options)).content)
        self.assertEqual(
            line_shapes(tree), [2, 1], "100mm + 40.6mm + 3.7mm 之后第三张放不下了，预览会换行，Word 不能硬塞"
        )
        self.assertEqual(widths_mm(tree), [100.0, 40.6, 40.6], "老师填的 100 就是纸面上的 100 毫米")

        # 再宽也不超过一整页：预览那一行本来就是 min(填的, 178mm 版心, 一页高)。
        # 一条 100x2000 的竖条就算填满版心，178mm 宽也意味着 3.56 米高，只能按一页高
        # 收成 10.5mm —— 预览里 flex 同样是这个结果。
        pub = self.publication(kind="free_response")
        folder = self.root / "library" / str(pub.id)
        folder.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 2000), "red").save(folder / "figure-1.png")
        pub.content["figures"] = [{"slot": "stem", "file": "figure-1.png", "page_idx": 0, "bbox": [0, 0, 100, 100]}]
        pub.content_hash = library.content_hash(pub.content)
        pub.save(update_fields=["content", "content_hash"])
        options = {"document": "questions", "figure_overrides": {str(pub.id): {"width": {"0": 178}}}}
        tree = document_xml(self.post(self.payload([pub], print_options=options)).content)
        self.assertEqual(widths_mm(tree), [10.5], "填满版心也不许超过一页高")

        # 靠左：整行贴着左边距，行末不再留空隙 —— flex 靠左时那一头本来就是敞开的，
        # 留着空隙会把整行推离页边 1.85mm。
        pub = build(3)
        options = {"document": "questions", "figure_overrides": {str(pub.id): {"align": "left"}}}
        tree = document_xml(self.post(self.payload([pub], print_options=options)).content)
        table = tree.find(".//w:tbl", NS)
        self.assertEqual(
            table.find("w:tblPr/w:jc", NS).get(f"{{{NS['w']}}}val"), "left", "老师选了靠左，整行就得真的靠左"
        )
        for cell in table.findall(".//w:tc", NS):
            self.assertEqual(
                cell.find("w:p/w:pPr/w:jc", NS).get(f"{{{NS['w']}}}val"),
                "left",
                "格里的图也要靠左，否则还是居中，行的位置跟着偏",
            )
        self.assertEqual(
            [round(value - 40.6, 1) for value in column_mm(table)],
            [3.7, 3.7, 0.0],
            "靠左时空隙只留在两张图之间，行末不留，否则整行会右移一个空隙",
        )

        # 靠右：反过来，行首不留空隙，整行贴着右页边。
        pub = build(3)
        options = {"document": "questions", "figure_overrides": {str(pub.id): {"align": "right"}}}
        tree = document_xml(self.post(self.payload([pub], print_options=options)).content)
        table = tree.find(".//w:tbl", NS)
        self.assertEqual(table.find("w:tblPr/w:jc", NS).get(f"{{{NS['w']}}}val"), "right")
        for cell in table.findall(".//w:tc", NS):
            self.assertEqual(cell.find("w:p/w:pPr/w:jc", NS).get(f"{{{NS['w']}}}val"), "right")
        self.assertEqual([round(value - 40.6, 1) for value in column_mm(table)], [0.0, 3.7, 3.7])

        # 一张图时走的是段落不是表格，对齐也得跟着变，否则单图永远是居中的。
        pub = build(1)
        for align, wanted_jc in (("left", "left"), ("right", "right"), ("center", "center")):
            with self.subTest(align=align):
                options = {"document": "questions", "figure_overrides": {str(pub.id): {"align": align}}}
                tree = document_xml(self.post(self.payload([pub], print_options=options)).content)
                self.assertIsNone(tree.find(".//w:tbl", NS))
                picture = tree.xpath("//w:p[w:r/w:drawing]", namespaces=NS)[0]
                self.assertEqual(picture.find("w:pPr/w:jc", NS).get(f"{{{NS['w']}}}val"), wanted_jc)

        # 点名换行：预览里插一道占满整行的空隙，Word 里就是多一张表。
        pub = build(3)
        options = {"document": "questions", "figure_overrides": {str(pub.id): {"breaks": [2]}}}
        tree = document_xml(self.post(self.payload([pub], print_options=options)).content)
        self.assertEqual(line_shapes(tree), [2, 1], "第 3 张要求另起一行，就是 2+1")

        pub = build(4)
        options = {"document": "questions", "figure_overrides": {str(pub.id): {"breaks": [1, 3]}}}
        tree = document_xml(self.post(self.payload([pub], print_options=options)).content)
        self.assertEqual(line_shapes(tree), [1, 2, 1], "四张图本来挤在一行，点名第 2、第 4 张另起一行，就断成 1+2+1")

        # 三样一起用，也是最像老师真实操作的一种：放大第三张、整行靠左、第二张换行。
        pub = build(3)
        options = {
            "document": "questions",
            "figure_overrides": {str(pub.id): {"width": {"2": 90}, "align": "left", "breaks": [2]}},
        }
        tree = document_xml(self.post(self.payload([pub], print_options=options)).content)
        self.assertEqual(line_shapes(tree), [2, 1])
        self.assertEqual(widths_mm(tree), [40.6, 40.6, 90.0])
        # 放大只改宽、高按图自己原来的比例跟上去 —— 90mm 宽的 240x120 图是 45mm 高。
        # 图被拉扁是最难一眼看出来的错：宽度对得上，比例不对，学生看不出来但一眼就别扭。
        self.assertEqual(heights_mm(tree), [20.3, 20.3, 45.0], "放大后要按图自己的比例长高，不能被压扁")
        tables = tree.findall(".//w:tbl", NS)
        self.assertEqual(
            [table.find("w:tblPr/w:jc", NS).get(f"{{{NS['w']}}}val") for table in tables],
            ["left", "left"],
            "两行都要靠左，预览里 justify-content 对每一行都生效",
        )

        # 别的题的设置不能串到这道题上。
        first, second = build(3), build(3)
        options = {
            "document": "questions",
            "figure_overrides": {str(first.id): {"align": "right", "width": {"0": 100}}},
        }
        tree = document_xml(self.post(self.payload([first, second], print_options=options)).content)
        tables = tree.findall(".//w:tbl", NS)
        # 第一题放大一张后是 2+1 两行，第二题三张图一行，一共三张表。
        self.assertEqual([len(table.findall("w:tblGrid/w:gridCol", NS)) for table in tables], [2, 1, 3])
        self.assertEqual(
            [table.find("w:tblPr/w:jc", NS).get(f"{{{NS['w']}}}val") for table in tables],
            ["right", "right", "center"],
            "没被调过的那道题仍然是居中，不能被上一道题的设置带跑",
        )

    def test_three_side_by_side_diagrams_stay_on_one_row(self):
        """The reported case: 初三「特殊的平行四边形」第 23 题.

        Three crops of one row on the original page — 279x226, 231x226 and
        210x236 px. At the preview's 150 dpi they are 178.56 / 147.84 / 134.4 px
        wide (measured in the running app), 460.8px plus two 14px gaps, which
        fits the 672.75px text area on one line. They must land on paper at
        those same proportions, on one row.
        """
        pub = self.publication(kind="free_response")
        folder = self.root / "library" / str(pub.id)
        folder.mkdir(parents=True, exist_ok=True)
        pub.content["figures"] = []
        for index, size in enumerate(((279, 226), (231, 226), (210, 236)), start=1):
            target = folder / f"figure-{index}.png"
            Image.new("RGB", size, "red").save(target)
            pub.content["figures"].append(
                {"slot": "stem", "file": target.name, "page_idx": 3, "bbox": [500 + index * 10, 228, 612, 334]}
            )
        pub.content_hash = library.content_hash(pub.content)
        pub.save(update_fields=["content", "content_hash"])

        response = self.post(self.payload([pub], print_options={"document": "questions"}))
        self.assertEqual(response.status_code, 200, response.content[:200])
        tree = document_xml(response.content)
        table = tree.find(".//w:tbl", NS)
        self.assertIsNotNone(table, "three diagrams must share one grid")
        self.assertEqual(len(table.findall("w:tblGrid/w:gridCol", NS)), 3, "they must stay on one row, not 2+1")
        self.assertEqual(len(table.findall("w:tr", NS)), 1)
        self.assertEqual(len(tree.findall(".//a:blip", NS)), 3)

        # 150 dpi, the size the preview writes onto the image itself:
        # 279 / 231 / 210 px × 25.4 / 150 = 47.2 / 39.1 / 35.6 mm.
        extents = [
            int(value) / 36000
            for value in tree.xpath(
                "//*[local-name()='extent' and namespace-uri()="
                "'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing']/@cx"
            )
        ]
        self.assertEqual([round(value, 1) for value in extents], [47.2, 39.1, 35.6])
        # Relative widths survive: the widest diagram is still the widest cell.
        columns_mm = [
            int(column.get(f"{{{NS['w']}}}w")) * 25.4 / 1440 for column in table.findall("w:tblGrid/w:gridCol", NS)
        ]
        self.assertEqual(len(columns_mm), 3)
        self.assertGreater(columns_mm[0], columns_mm[1])
        self.assertGreater(columns_mm[1], columns_mm[2])
        # Each column carries the gap the preview shows between figures. It is
        # not optional on the last column either: the pictures sit centred in
        # their cells, so a bare last column leaves 1.85mm more space on the
        # left of the row than on the right — Word 16.0 measured that on the
        # real export before the trailing gap was added.
        for position, (column_mm, picture_mm) in enumerate(zip(columns_mm, extents)):
            self.assertGreater(
                column_mm - picture_mm,
                1.5,
                f"column {position + 1} ({column_mm} mm) has no room for its {picture_mm} mm picture",
            )
        # Centred, like .qb-figures, and still inside the 178mm text area.
        self.assertEqual(table.find("w:tblPr/w:jc", NS).get(f"{{{NS['w']}}}val"), "center")
        self.assertLessEqual(sum(columns_mm), 178)
        # The whole point: the pictures and the gaps between them are exactly
        # what the teacher checked on screen. 178.56 + 147.84 + 134.4 px of
        # pictures plus two 14px gaps = 488.8 px, and 488.8 / 3.7795 = 129.3 mm.
        # The grid carries one extra gap at each end, which is the empty space
        # that puts the content in the middle.
        content_mm = sum(columns_mm) - export.PREVIEW_FIGURE_GAP_PX * 25.4 / 96
        self.assertAlmostEqual(content_mm, 129.3, delta=0.2, msg="the row must be the same width as the preview row")

        # Centring only means something with these three in place. Word pads each
        # cell by 1.9mm a side, which measured 4mm of rightward drift, and it
        # re-infers a table marked "auto" width from the content instead of
        # centring the columns it was given.
        for side in ("top", "left", "bottom", "right"):
            edge = table.find(f"w:tblPr/w:tblCellMar/w:{side}", NS)
            self.assertIsNotNone(edge, f"the grid must declare its own {side} cell padding")
            self.assertEqual(edge.get(f"{{{NS['w']}}}w"), "0", f"{side} cell padding")
        declared = table.find("w:tblPr/w:tblW", NS)
        self.assertEqual(declared.get(f"{{{NS['w']}}}type"), "dxa", "the table width must be stated, not left to Word")
        self.assertAlmostEqual(int(declared.get(f"{{{NS['w']}}}w")) * 25.4 / 1440, sum(columns_mm), delta=0.2)

        # w:tblPr is a fixed sequence. Borders appended at the end landed after
        # w:tblLook, which is not a valid document; Word then does whatever it
        # likes with the whole block, including the centring.
        order = [
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
        ]
        positions = [order.index(node.tag.split("}")[1]) for node in table.find("w:tblPr", NS)]
        self.assertEqual(
            positions,
            sorted(positions),
            f"w:tblPr children out of order: {[n.tag.split('}')[1] for n in table.find('w:tblPr', NS)]}",
        )

    def test_a_single_stem_figure_stays_a_plain_paragraph(self):
        pub = self.publication(kind="free_response")
        self.figure(pub)
        response = self.post(self.payload([pub], print_options={"document": "questions"}))
        self.assertEqual(response.status_code, 200)
        tree = document_xml(response.content)
        self.assertIsNone(tree.find(".//w:tbl", NS))
        self.assertEqual(len(tree.findall(".//a:blip", NS)), 1)

    def test_the_name_class_score_line_is_centred_like_the_preview_centres_it(self):
        """姓名／班级／得分 is the one line that was left in Word only.

        The preview centres it (``.print-info`` is ``text-align: center``), so the
        PDF follows, and the teacher checks it on screen before exporting. Word
        never got the alignment, so the download put the line against the left
        margin while the title right above it stayed centred — the single
        most visible difference on the whole page.
        """
        pub = self.publication(kind="free_response")
        for student_info in (True, False):
            with self.subTest(student_info=student_info):
                response = self.post(
                    self.payload([pub], print_options={"document": "questions", "student_info": student_info})
                )
                self.assertEqual(response.status_code, 200)
                line = None
                for paragraph in tree_paras(document_xml(response.content)):
                    if "姓名：" in "".join(paragraph.itertext()):
                        line = paragraph
                        break
                if not student_info:
                    self.assertIsNone(line, "the line is switched off, so it must be absent")
                    continue
                self.assertIsNotNone(line, "the 姓名／班级／得分 line is missing")
                justification = line.find("w:pPr/w:jc", NS)
                self.assertIsNotNone(justification, "the line carries no alignment at all")
                self.assertEqual(
                    justification.get(f"{{{NS['w']}}}val"),
                    "center",
                    "姓名／班级／得分 must land in the middle, not against the margin",
                )

    def test_a_lone_stem_figure_is_centred_like_the_preview_centres_it(self):
        """A single picture is a .qb-figures row of one, so it is centred.

        Word 16.0 was asked directly where a lone 50mm diagram lands: against
        the left margin, paragraph alignment left, with 128mm of blank page to
        its right. The preview puts it in the middle with 242px of space on each
        side. The grid case has its own test; this is the one-figure case.
        """
        pub = self.publication(kind="free_response")
        self.figure(pub)
        response = self.post(self.payload([pub], print_options={"document": "questions"}))
        self.assertEqual(response.status_code, 200)
        tree = document_xml(response.content)
        drawing = tree.find(".//w:drawing", NS)
        self.assertIsNotNone(drawing)
        paragraph = next(node for node in tree.iter(f"{{{NS['w']}}}p") if node.find(".//w:drawing", NS) is not None)
        justification = paragraph.find("w:pPr/w:jc", NS)
        self.assertIsNotNone(justification, "a lone figure must be centred, not left on the margin")
        self.assertEqual(justification.get(f"{{{NS['w']}}}val"), "center")

    def test_a_scanned_page_stays_left_because_the_preview_leaves_it_left(self):
        """The mirror image: .qb-question-images is a column flex box.

        A picture with a set width in a column flex container sits at the start
        of the cross axis, so the preview leaves scanned pages at the left.
        Centring them here would introduce the very mismatch this test guards.
        """
        pub = self.publication(kind="single_choice", stem="")
        folder = self.root / "library" / str(pub.id)
        folder.mkdir(parents=True, exist_ok=True)
        images = []
        for index in (1, 2):
            target = folder / f"question-{index}.png"
            Image.new("RGB", (240, 120), "red").save(target)
            images.append(
                {
                    "file": target.name,
                    "width": 240,
                    "height": 120,
                    "page_idx": index - 1,
                    "bbox": [0, 0, 100, 100],
                    "order": index - 1,
                    "image_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            )
        pub.content.update(body_mode="source_image", question_images=images, figures=[])
        pub.content_hash = library.content_hash(pub.content)
        pub.save(update_fields=["content", "content_hash"])
        response = self.post(self.payload([pub], print_options={"document": "questions"}))
        self.assertEqual(response.status_code, 200)
        tree = document_xml(response.content)
        for paragraph in [node for node in tree.iter(f"{{{NS['w']}}}p") if node.find(".//w:drawing", NS) is not None]:
            self.assertIsNone(
                paragraph.find("w:pPr/w:jc", NS), "a scanned page is left in the preview, so it stays left here"
            )

    def test_source_image_papers_keep_one_picture_per_line(self):
        # A scanned page is not a diagram: the preview stacks those one per line
        # (.qb-question-images is a column), so the download must not grid them.
        pub = self.publication(kind="single_choice", stem="")
        folder = self.root / "library" / str(pub.id)
        folder.mkdir(parents=True, exist_ok=True)
        images = []
        for index in range(1, 5):
            target = folder / f"question-{index}.png"
            Image.new("RGB", (240, 120), "red").save(target)
            images.append(
                {
                    "file": target.name,
                    "width": 240,
                    "height": 120,
                    "page_idx": index - 1,
                    "bbox": [0, 0, 100, 100],
                    "order": index - 1,
                    "image_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            )
        pub.content.update(body_mode="source_image", question_images=images, figures=[])
        pub.content_hash = library.content_hash(pub.content)
        pub.save(update_fields=["content", "content_hash"])
        response = self.post(self.payload([pub], print_options={"document": "questions"}))
        self.assertEqual(response.status_code, 200, response.content[:200])
        tree = document_xml(response.content)
        self.assertIsNone(tree.find(".//w:tbl", NS), "a scanned page keeps one picture per line")
        self.assertEqual(len(tree.findall(".//a:blip", NS)), 4)

    def test_stem_and_option_figures_each_get_their_own_grid(self):
        # The stem row and the option grid are separate: each follows the
        # preview for its own area, and neither swallows the other's pictures.
        pub = self.publication(kind="single_choice", options=dict.fromkeys("ABCD", ""))
        folder = self.root / "library" / str(pub.id)
        folder.mkdir(parents=True, exist_ok=True)
        pub.content["figures"] = []
        for index, slot in enumerate(["stem"] * 4 + list("ABCD"), 1):
            target = folder / f"figure-{index}.png"
            Image.new("RGB", (240, 120), "red").save(target)
            pub.content["figures"].append({"slot": slot, "file": target.name, "page_idx": 0, "bbox": [0, 0, 100, 100]})
        pub.content_hash = library.content_hash(pub.content)
        pub.save(update_fields=["content", "content_hash"])
        response = self.post(self.payload([pub], print_options={"document": "questions"}))
        self.assertEqual(response.status_code, 200, response.content[:200])
        tree = document_xml(response.content)
        self.assertEqual(len(tree.findall(".//a:blip", NS)), 8)
        tables = tree.findall(".//w:tbl", NS)
        self.assertEqual(len(tables), 2, "one grid for the stem figures, one for the options")
        # Four 240px stem diagrams fit on one line in the preview, so they do
        # here too; the four option diagrams still answer to the option grid.
        self.assertEqual(len(tables[0].findall("w:tblGrid/w:gridCol", NS)), 4)
        self.assertEqual(len(tables[1].findall("w:tblGrid/w:gridCol", NS)), 2)
        for table in tables:
            self.assertEqual(len(table.findall(".//a:blip", NS)), 4)
        self.assertEqual(len(tables[0].findall("w:tr", NS)), 1)
        self.assertEqual(len(tables[1].findall("w:tr", NS)), 2)

    def test_inline_missing_solution_does_not_emit_a_separate_placeholder(self):
        complete = self.publication(answer="2", analysis="有答案的过程")
        missing = self.publication(stem="末尾无解答题干")
        payload = self.payload([complete, missing], print_options={"document": "combined", "answer_layout": "inline"})
        response = self.post(payload)
        self.assertEqual(response.status_code, 200)
        text = "".join(document_xml(response.content).itertext())
        self.assertIn("末尾无解答题干", text)
        self.assertIn("有答案的过程", text)
        self.assertNotIn("未提供", text)
        # The numbered answer sheet continues to identify missing solutions.
        response = self.post(self.payload([complete, missing], print_options={"document": "answers"}))
        text = "".join(document_xml(response.content).itertext())
        self.assertIn("未提供", text)

    def test_change_during_build_never_returns_an_old_or_partial_file(self):
        pub = self.publication(answer="原答案")
        original = export._document
        for mutation in (
            lambda: PublishedQuestion.objects.filter(pk=pub.id).update(status="withdrawn"),
            lambda: PublishedQuestion.objects.filter(pk=pub.id).update(content={**pub.content, "stem": "另一版"}),
        ):
            pub.status = "published"
            pub.save(update_fields=["content", "status"])

            def changed(*args, _mutation=mutation, **kwargs):
                data = original(*args, **kwargs)
                _mutation()
                return data

            with mock.patch.object(export, "_document", side_effect=changed):
                response = self.post(self.payload([pub]))
            self.assertEqual(response.status_code, 409)
            self.assertNotIn("Content-Disposition", response)

    def test_ai_result_and_feature_change_during_build_abort(self):
        features.save({"ai_answer": True})
        pub = self.publication(extras={"ai_answer": {"answer": "参考"}})
        original = export._document
        payload = self.payload([pub], print_options={"ai_answers": True})

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            features.save({"ai_answer": False})
            return result

        with mock.patch.object(export, "_document", side_effect=changed):
            self.assertEqual(self.post(payload).status_code, 409)
        features.save({"ai_answer": True})

        def changed_answer(*args, **kwargs):
            result = original(*args, **kwargs)
            PublishedQuestion.objects.filter(pk=pub.id).update(extras={"ai_answer": {"answer": "新参考"}})
            return result

        with mock.patch.object(export, "_document", side_effect=changed_answer):
            self.assertEqual(self.post(payload).status_code, 409)

    def test_image_bytes_change_during_build_abort(self):
        pub = self.publication()
        self.figure(pub)
        original = export._document

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.figure(pub, color="blue")
            return result

        with mock.patch.object(export, "_document", side_effect=changed):
            self.assertEqual(self.post(self.payload([pub])).status_code, 409)

    def test_request_guards_formats_limits_and_filename(self):
        pub = self.publication()
        payload = self.payload([pub], title="CON")
        self.assertEqual(self.client.get("/api/library/export-docx").status_code, 405)
        self.assertEqual(
            self.client.post(
                "/api/library/export-docx", json.dumps(payload), content_type="application/json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/library/export-docx", json.dumps(payload), content_type="text/plain", HTTP_X_QB_REQUEST="1"
            ).status_code,
            415,
        )
        self.assertEqual(self.post(payload, REMOTE_ADDR="192.0.2.1").status_code, 403)
        self.assertEqual(self.post(payload, REMOTE_ADDR="::1").status_code, 200)
        self.assertEqual(export.safe_filename("CON"), "练习-CON")
        self.assertEqual(export.safe_filename("练习/一?"), "练习_一_")
        for change in (
            {"format": []},
            {"ids": []},
            {"ids": [str(pub.id), str(pub.id)]},
            {"print_options": {"font_size": True}},
            {"print_options": {"student_info": 1}},
            {"private_key": "synthetic"},
        ):
            self.assertEqual(self.post({**payload, **change}).status_code, 400)
        response = self.client.post(
            "/api/library/export-docx",
            '"' + "x" * (export.MAX_BODY + 1) + '"',
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(response.status_code, 413)

    def test_invalid_xml_characters_and_malformed_answer_snapshot_are_specific_errors(self):
        pub = self.publication()
        for value in ("卷名\uffff", "卷名\ud800"):
            response = self.post(self.payload([pub], title=value))
            self.assertEqual(response.status_code, 400)
        pub.content["analysis"] = {"corrupt": True}
        pub.save(update_fields=["content"])
        self.assertEqual(self.post(self.payload([pub])).status_code, 409)


class DraftPrintCompatibilityTests(TestCase):
    def test_legacy_and_explicit_documents_and_validation(self):
        # 1.12.9：新建草稿默认只出题目卷。老草稿里显式写着 answers 的照旧尊重 ——
        # 里面那句 answers 推回 combined 的规则必须留着，否则用户当年存的
        # 「题目＋答案」会在打开时被悄悄改成题目卷。
        self.assertEqual(library_drafts.PRINT_DEFAULTS["document"], "questions")
        self.assertEqual(library_drafts.PRINT_DEFAULTS["answers"], False)
        self.assertEqual(library_drafts._print_options({})["document"], "questions")
        self.assertEqual(library_drafts._print_options({"answers": False})["document"], "questions")
        self.assertEqual(library_drafts._print_options({"answers": True})["document"], "combined")
        self.assertEqual(library_drafts._print_options({"answers": False, "document": "answers"})["answers"], True)
        for bad in (
            {"font_size": 13},
            {"font_size": True},
            {"document": "other"},
            {"answer_space": "other"},
            {"origin": 1},
        ):
            with self.assertRaises(library_drafts.DraftError):
                library_drafts._print_options(bad)

    def test_old_schema1_is_read_in_memory_without_rewriting_file(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(DATA_ROOT=Path(directory)):
            record = {
                "id": str(uuid.uuid4()),
                "title": "旧草稿",
                "ids": [],
                "print_options": {"answers": False, "origin": True, "ai_answers": False},
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "revision": 1,
            }
            data = json.dumps({"schema": 1, "drafts": [record]}, ensure_ascii=False).encode("utf-8")
            target = Path(directory) / "library-drafts.json"
            target.write_bytes(data)
            result = library_drafts._read()["drafts"][0]
            self.assertEqual(result["print_options"]["document"], "questions")
            self.assertEqual(result["print_options"]["font_size"], 12)
            self.assertEqual(target.read_bytes(), data)

    def test_new_print_options_roundtrip_and_partial_update_keep_layout(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(DATA_ROOT=Path(directory)):
            first = library_drafts._save(
                {
                    "title": "新版草稿",
                    "ids": [],
                    "print_options": {
                        "document": "answers",
                        "font_size": 16,
                        "answer_space": "large",
                        "student_info": False,
                    },
                }
            )
            stored = library_drafts._read()["drafts"][0]
            self.assertEqual(stored["print_options"], first["print_options"])
            second = library_drafts._save({"print_options": {"origin": True}}, first["id"])
            self.assertEqual(second["print_options"]["document"], "answers")
            self.assertEqual(second["print_options"]["font_size"], 16)
            self.assertFalse(second["print_options"]["student_info"])

    def test_layout_defaults_are_independent_and_old_store_stays_unchanged(self):
        first = library_drafts._print_options({})
        self.assertEqual((first["pagination"], first["option_layout"]), ("compact", "auto"))
        first["option_overrides"][str(uuid.uuid4())] = "four"
        first["question_breaks"].append(str(uuid.uuid4()))
        self.assertEqual(library_drafts._print_options({})["option_overrides"], {})
        self.assertEqual(library_drafts._print_options({})["question_breaks"], [])

    def test_layout_hints_validate_types_membership_duplicates_and_limit(self):
        key, other = str(uuid.uuid4()), str(uuid.uuid4())
        normalized = library_drafts._print_options(
            {"option_overrides": {key.upper(): "two"}, "question_breaks": [key.upper()]}, ids=[key]
        )
        self.assertEqual(normalized["option_overrides"], {key: "two"})
        self.assertEqual(normalized["question_breaks"], [key])
        for invalid in (
            {"pagination": "other"},
            {"option_layout": False},
            {"option_overrides": []},
            {"option_overrides": {key: False}},
            {"option_overrides": {key: "three"}},
            {"option_overrides": {other: "four"}},
            {"option_overrides": {key: "four", key.upper(): "two"}},
            {"question_breaks": key},
            {"question_breaks": [other]},
            {"question_breaks": [key, key]},
            {"question_breaks": [str(uuid.uuid4()) for _ in range(501)]},
        ):
            with self.assertRaises(library_drafts.DraftError):
                library_drafts._print_options(invalid, ids=[key])

    def test_layout_hints_roundtrip_and_removed_question_hints_are_cleaned_atomically(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(DATA_ROOT=Path(directory)):
            first, second = str(uuid.uuid4()), str(uuid.uuid4())
            record = library_drafts._save(
                {
                    "ids": [first, second],
                    "print_options": {
                        "pagination": "keep",
                        "option_layout": "four",
                        "option_overrides": {second: "two"},
                        "question_breaks": [second],
                    },
                }
            )
            self.assertEqual(library_drafts._read()["drafts"][0]["print_options"], record["print_options"])
            changed = library_drafts._save({"print_options": {"font_size": 16}}, record["id"])
            self.assertEqual(changed["print_options"]["option_overrides"], {second: "two"})
            removed = library_drafts._save({"ids": [first]}, record["id"])
            self.assertEqual(removed["print_options"]["option_overrides"], {})
            self.assertEqual(removed["print_options"]["question_breaks"], [])
            before = library_drafts.draft_path().read_bytes()
            with self.assertRaises(library_drafts.DraftError):
                library_drafts._save({"print_options": {"question_breaks": [second]}}, record["id"])
            self.assertEqual(library_drafts.draft_path().read_bytes(), before)

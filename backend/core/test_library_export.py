"""Offline export contract, native Word structures and fail-closed downloads."""

from __future__ import annotations

from copy import deepcopy
import io
import json
import os
import tempfile
import uuid
import zipfile
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import path
from lxml import etree
from PIL import Image

from . import features, library, library_drafts, library_export as export
from .models import Paper, PublishedQuestion, Question

urlpatterns = [path("api/library/export-docx", export.export_docx_view)]
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
      "m": export.OMML_NS, "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def units(value):
    return len(value.encode("utf-16-le")) // 2


def field(value):
    return {"source": value, "blocks": [{"type": "text", "start": 0, "end": units(value),
            "segments": [{"type": "text", "start": 0, "end": units(value)}]}] if value else []}


def math_field(latex, body, *, display=False):
    source = ("$$" if display else "$") + latex + ("$$" if display else "$")
    mathml = f'<math xmlns="{export.MATH_NS}"><semantics>{body}<annotation encoding="application/x-tex">' \
             + __import__("html").escape(latex) + '</annotation></semantics></math>'
    return {"source": source, "blocks": [{"type": "text", "start": 0, "end": units(source), "segments": [
        {"type": "math", "start": 0, "end": units(source), "latex": latex, "mathml": mathml, "display": display}]}]}


def document_xml(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return etree.fromstring(archive.read("word/document.xml"))


@override_settings(ROOT_URLCONF=__name__)
class LibraryExportTests(TestCase):
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

    def publication(self, *, stem="离线合成题", kind="free_response", answer="", analysis="", options=None,
                    origin="虚构出处", status="published", extras=None):
        self.counter += 1
        paper = Paper.objects.create(filename="测试合成卷.pdf", kind="pdf", sha256="a" * 64)
        question = Question.objects.create(paper=paper, number=self.counter, question_type=kind, stem=stem,
                                           answer=answer, analysis=analysis, options=options or {}, state="green")
        content = {"stem": stem, "answer": answer, "analysis": analysis, "options": options or {},
                   "question_type": kind, "origin": origin, "figures": [], "sources": [],
                   "document_id": str(paper.id), "source_filename": paper.filename}
        return PublishedQuestion.objects.create(paper=paper, question=question, number=self.counter,
            source_filename=paper.filename, question_type=kind, version=1, status=status,
            content=content, content_hash=library.content_hash(content), extras=extras or {})

    def payload(self, pubs, *, print_options=None, output_format="docx", title="单元练习"):
        options = library_drafts._print_options(print_options or {})
        fields = {}
        for publication in pubs:
            content = publication.content
            selected, _ = export._selected(content, publication.extras, options["ai_answers"] and features.enabled("ai_answer"))
            fields[str(publication.id)] = {name: field(str(content.get(name) or "")) for name in ("stem", "origin")}
            fields[str(publication.id)].update({f"options.{key}": field(value) for key, value in (content.get("options") or {}).items()})
            fields[str(publication.id)].update({name: field(str(selected.get(name) or "")) for name in ("answer", "analysis")})
        return {"ids": [str(pub.id) for pub in pubs], "title": title, "print_options": print_options or {},
                "rendered_fields": fields, "format": output_format}

    def post(self, payload, **changes):
        return self.client.post("/api/library/export-docx", json.dumps(payload), content_type="application/json",
                                HTTP_X_QB_REQUEST="1", **changes)

    def figure(self, publication, slot="stem", color="red"):
        folder = self.root / "library" / str(publication.id)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "figure-1.png"
        Image.new("RGB", (240, 120), color).save(target)
        publication.content["figures"] = [{"slot": slot, "file": target.name, "page_idx": 0, "bbox": [0, 0, 100, 100]}]
        publication.content_hash = library.content_hash(publication.content)
        publication.save(update_fields=["content", "content_hash"])
        return target

    def test_docx_is_complete_editable_and_does_not_write_question_data(self):
        first = self.publication(kind="free_response", answer="2", analysis="离线解析")
        second = self.publication(kind="single_choice", answer="A", options={"A": "甲", "B": "乙", "C": "丙", "D": "丁"})
        self.figure(first)
        before = list(PublishedQuestion.objects.values())
        questions = list(Question.objects.values())
        result = self.post(self.payload([first, second], print_options={"document": "combined", "origin": True, "font_size": 14, "answer_space": "large"}))
        self.assertEqual(result.status_code, 200, result.content[:300])
        self.assertEqual(result["X-Question-Count"], "2")
        self.assertIn("filename*=UTF-8''", result["Content-Disposition"])
        self.assertEqual(result["Cache-Control"], "no-store")
        self.assertEqual(result["Content-Type"], "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
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
        pub = self.publication(answer="答案", extras={"ai_answer": {"answer": "不同AI答案"}})
        response = self.post(self.payload([pub], output_format="split", print_options={"document": "questions"}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(len(archive.namelist()), 2)
            questions = "".join(document_xml(archive.read("单元练习-题目卷.docx")).itertext())
            answers = "".join(document_xml(archive.read("单元练习-答案解析卷.docx")).itertext())
        self.assertNotIn("参考答案与解析", questions)
        self.assertIn("答案", answers)
        self.assertNotIn("不同AI答案", answers)
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
        for edit in (lambda seg: seg.update(end=2), lambda seg: seg.update(type="delimiter"),
                     lambda seg: seg.update(start=1), lambda seg: seg.update(auto=True)):
            payload = self.payload([pub])
            edit(payload["rendered_fields"][str(pub.id)]["stem"]["blocks"][0]["segments"][0])
            self.assertEqual(self.post(payload).status_code, 400)

    def test_special_symbols_and_blank_are_editable_text_and_cover_original(self):
        source = "$▱ABCD$ ___ （ ）"
        pub = self.publication(stem=source)
        value = {"source": source, "blocks": [{"type": "text", "start": 0, "end": units(source), "segments": [
            {"type": "delimiter", "start": 0, "end": 1}, {"type": "parallelogram", "start": 1, "end": 2},
            {"type": "text", "start": 2, "end": 6}, {"type": "delimiter", "start": 6, "end": 7},
            {"type": "text", "start": 7, "end": 8}, {"type": "blank", "start": 8, "end": 11},
            {"type": "text", "start": 11, "end": 12}, {"type": "bracket", "start": 12, "end": 15}]}]}
        payload = self.payload([pub])
        payload["rendered_fields"][str(pub.id)]["stem"] = value
        result = self.post(payload)
        self.assertEqual(result.status_code, 200, result.content[:300])
        text = "".join(document_xml(result.content).itertext())
        self.assertIn("▱ABCD", text)
        self.assertIn("________", text)
        self.assertNotIn("$", text)

    def test_square_root_and_fraction_become_native_editable_omml(self):
        value = math_field(r"\sqrt{x+1}+\frac{a}{b}", '<mrow><msqrt><mrow><mi>x</mi><mo>+</mo><mn>1</mn></mrow></msqrt><mo>+</mo><mfrac><mi>a</mi><mi>b</mi></mfrac></mrow>', display=True)
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
        self.assertEqual(rad.find("m:radPr/m:degHide", NS).get(f'{{{export.OMML_NS}}}val'), "1")
        self.assertIn("x+1", "".join(rad.itertext()))
        self.assertNotIn("\\sqrt", "".join(xml.itertext()))

    def test_stretchy_arrow_known_upstream_schema_typo_is_repaired(self):
        value = math_field(r"\overrightarrow{AB}", '<mover><mrow><mi>A</mi><mi>B</mi></mrow><mo stretchy="true">→</mo></mover>')
        converted = export._field(value, value["source"], "合成公式")
        math = converted[0]["segments"][0]["omml"]
        self.assertTrue(math.findall(".//m:groupChrPr", NS))
        self.assertIn("AB", "".join(math.itertext()))

    def test_vector_hat_bar_and_nested_accents_are_native_accents(self):
        for command, glyph in (("vec", "⃗"), ("hat", "^"), ("bar", "ˉ"), ("dot", "˙"), ("tilde", "~"), ("overrightarrow", "→")):
            value = math_field("\\" + command + "{AB}", f'<mover accent="true"><mrow><mi>A</mi><mi>B</mi></mrow><mo>{glyph}</mo></mover>')
            math = export._field(value, value["source"], "合成重音")[0]["segments"][0]["omml"]
            accents = math.findall(".//m:acc", NS)
            self.assertEqual(len(accents), 1)
            self.assertEqual(accents[0].find("m:accPr/m:chr", NS).get(f'{{{export.OMML_NS}}}val'), export.ACCENTS[glyph])
            self.assertFalse(math.findall(".//m:limUpp", NS))
            self.assertIn("AB", "".join(math.itertext()))
        value = math_field(r"\hat{\vec{x}}", '<mover accent="true"><mover accent="true"><mi>x</mi><mo>⃗</mo></mover><mo>^</mo></mover>')
        math = export._field(value, value["source"], "合成重音")[0]["segments"][0]["omml"]
        self.assertEqual(len(math.findall(".//m:acc", NS)), 2)
        # A genuine overscript is a limit, not an accent.
        value = math_field(r"\overset{a}{x}", '<mover><mi>x</mi><mi>a</mi></mover>')
        math = export._field(value, value["source"], "合成重音")[0]["segments"][0]["omml"]
        self.assertTrue(math.findall(".//m:limUpp", NS))
        self.assertFalse(math.findall(".//m:acc", NS))

    def test_cancellation_is_rejected_instead_of_changing_to_a_box(self):
        for command, notation in (("cancel", "updiagonalstrike"), ("bcancel", "downdiagonalstrike"),
                                  ("xcancel", "updiagonalstrike downdiagonalstrike")):
            # Exact MathML enclosure shape produced by standard KaTeX.
            value = math_field("\\" + command + "{x}", f'<menclose notation="{notation}"><mi>x</mi></menclose>')
            with self.assertRaises(export.ExportError):
                export._field(value, value["source"], "合成划消公式")
        boxed = math_field(r"\boxed{x}", '<menclose notation="box"><mi>x</mi></menclose>')
        self.assertTrue(export._field(boxed, boxed["source"], "合成方框")[0]["segments"][0]["omml"].findall(".//m:borderBox", NS))

    def test_question_paragraphs_keep_together_and_release_the_final_chain(self):
        pub = self.publication(kind="single_choice", options={"A": "甲", "B": "乙"})
        self.figure(pub, slot="A")
        response = self.post(self.payload([pub], print_options={"document": "questions", "student_info": False, "pagination": "keep"}))
        self.assertEqual(response.status_code, 200)
        xml = document_xml(response.content)
        paragraphs = xml.find("w:body", NS).findall("w:p", NS)
        question = [paragraph for paragraph in paragraphs if "离线合成题" in "".join(paragraph.itertext())][0]
        self.assertEqual(question.find("w:pPr/w:keepNext", NS).get(f'{{{NS["w"]}}}val'), "1")
        last_option = [paragraph for paragraph in paragraphs if "B. 乙" in "".join(paragraph.itertext())][0]
        self.assertEqual(last_option.find("w:pPr/w:keepNext", NS).get(f'{{{NS["w"]}}}val'), "0")

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
        with mock.patch.object(export, "_convert_math_root", side_effect=AssertionError("Word converter must not run for PDF")):
            captured, use_ai = export._capture(payload["ids"], payload["rendered_fields"], options, "pdf")
        segment = captured[0]["fields"]["stem"][0]["segments"][0]
        self.assertIsNone(segment["omml"])
        self.assertEqual(segment["mathml"], value["blocks"][0]["segments"][0]["mathml"])
        self.assertEqual(segment["latex"], r"\cancel{x}")
        export._recheck(captured, options, use_ai)
        self.assertEqual(self.post(payload).status_code, 400)
        # Skipping OMML compatibility never skips XML, source or annotation checks.
        for mutation in (lambda raw: raw.replace("<mi>x</mi>", "<unsupported>x</unsupported>"),
                         lambda raw: raw.replace("<mi>x</mi>", '<mi onclick="bad">x</mi>'),
                         lambda raw: '<!DOCTYPE math>' + raw):
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
        question = [paragraph for paragraph in paragraphs if any(word in "".join(paragraph.itertext())
                    for word in ("合成题开头", "第一小问", "第二小问"))]
        self.assertEqual(len(question), 3)
        for paragraph in question:
            self.assertEqual(paragraph.find("w:pPr/w:keepLines", NS).get(f'{{{NS["w"]}}}val'), "0")
            self.assertEqual(paragraph.find("w:pPr/w:keepNext", NS).get(f'{{{NS["w"]}}}val'), "0")
        self.assertEqual(question[-1].find("w:pPr/w:spacing", NS).get(f'{{{NS["w"]}}}after'), "160")

    def test_compact_protects_display_formula_and_picture_without_locking_long_prose(self):
        formula = math_field(r"\frac{a}{b}", '<mfrac><mi>a</mi><mi>b</mi></mfrac>', display=True)
        before, after = "长段开头" * 40 + "\n", "\n结束文字"
        source = before + formula["source"] + after
        pub = self.publication(stem=source)
        self.figure(pub)
        payload = self.payload([pub], print_options={"document": "questions"})
        math = deepcopy(formula["blocks"][0]["segments"][0])
        math.update(start=units(before), end=units(before + formula["source"]))
        payload["rendered_fields"][str(pub.id)]["stem"] = {"source": source, "blocks": [{"type": "text", "start": 0,
            "end": units(source), "segments": [{"type": "text", "start": 0, "end": units(before)}, math,
            {"type": "text", "start": units(before + formula["source"]), "end": units(source)}]}]}
        response = self.post(payload)
        self.assertEqual(response.status_code, 200)
        paragraphs = document_xml(response.content).find("w:body", NS).findall("w:p", NS)
        for paragraph in paragraphs:
            if paragraph.find("m:oMath", NS) is not None or paragraph.find(".//w:drawing", NS) is not None:
                self.assertEqual(paragraph.find("w:pPr/w:keepLines", NS).get(f'{{{NS["w"]}}}val'), "1")
        prose = next(paragraph for paragraph in paragraphs if "长段开头" in "".join(paragraph.itertext()))
        self.assertEqual(prose.find("w:pPr/w:keepNext", NS).get(f'{{{NS["w"]}}}val'), "0")
        self.assertEqual(prose.find("w:pPr/w:keepLines", NS).get(f'{{{NS["w"]}}}val'), "0")

    def test_blank_source_lines_add_spacing_without_empty_word_paragraphs(self):
        formula = math_field(r"\frac{a}{b}", '<mfrac><mi>a</mi><mi>b</mi></mfrac>', display=True)
        before, after = "题干开头\n\n  \n(1) 保留答题线________\n\n", "\n\n  \n(2) 保留第二小问\n\n"
        source = before + formula["source"] + after
        pub = self.publication(stem=source)
        payload = self.payload([pub], print_options={"document": "questions", "student_info": False})
        math = deepcopy(formula["blocks"][0]["segments"][0])
        math.update(start=units(before), end=units(before + formula["source"]))
        payload["rendered_fields"][str(pub.id)]["stem"] = {"source": source, "blocks": [{"type": "text", "start": 0,
            "end": units(source), "segments": [{"type": "text", "start": 0, "end": units(before)}, math,
            {"type": "text", "start": units(before + formula["source"]), "end": units(source)}]}]}
        response = self.post(payload)
        self.assertEqual(response.status_code, 200, response.content[:200])
        paragraphs = document_xml(response.content).find("w:body", NS).findall("w:p", NS)
        question = paragraphs[2:]  # title, section heading
        self.assertEqual(len(question), 4)
        self.assertEqual(sum(paragraph.find("m:oMath", NS) is not None for paragraph in question), 1)
        self.assertFalse(any(not "".join(paragraph.itertext()).strip() and paragraph.find("m:oMath", NS) is None
                             for paragraph in question))
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
        payload["rendered_fields"][str(wide_math.id)]["stem"] = {"source": wide_math.content["stem"], "blocks": [
            {"type": "text", "start": 0, "end": units(wide_math.content["stem"]), "segments": [
                {"type": "text", "start": 0, "end": units(prefix)}, math]}]}
        response = self.post(payload)
        self.assertEqual(response.status_code, 200)
        paragraphs = document_xml(response.content).find("w:body", NS).findall("w:p", NS)
        short_paragraph = next(paragraph for paragraph in paragraphs if "短图注请看下图" in "".join(paragraph.itertext()))
        long_paragraph = next(paragraph for paragraph in paragraphs if "多行题干材料" in "".join(paragraph.itertext()))
        math_paragraph = next(paragraph for paragraph in paragraphs if "短文字与长公式" in "".join(paragraph.itertext()))
        self.assertEqual(short_paragraph.find("w:pPr/w:keepNext", NS).get(f'{{{NS["w"]}}}val'), "1")
        self.assertEqual(long_paragraph.find("w:pPr/w:keepNext", NS).get(f'{{{NS["w"]}}}val'), "0")
        self.assertEqual(math_paragraph.find("w:pPr/w:keepNext", NS).get(f'{{{NS["w"]}}}val'), "0")
        for paragraph in paragraphs:
            if paragraph.find(".//w:drawing", NS) is not None:
                self.assertEqual(paragraph.find("w:pPr/w:keepLines", NS).get(f'{{{NS["w"]}}}val'), "1")

    def test_keep_long_question_falls_back_to_breakable_paragraphs(self):
        pub = self.publication(stem="\n".join(f"第 {index} 段合成材料" * 12 for index in range(60)))
        response = self.post(self.payload([pub], print_options={"document": "questions", "pagination": "keep"}))
        self.assertEqual(response.status_code, 200)
        xml = document_xml(response.content)
        prose = next(paragraph for paragraph in xml.find("w:body", NS).findall("w:p", NS) if "第 1 段合成材料" in "".join(paragraph.itertext()))
        self.assertEqual(prose.find("w:pPr/w:keepNext", NS).get(f'{{{NS["w"]}}}val'), "0")
        self.assertEqual(prose.find("w:pPr/w:keepLines", NS).get(f'{{{NS["w"]}}}val'), "0")

    def test_global_and_per_question_columns_and_font_size_safe_fallback(self):
        first = self.publication(kind="single_choice", options={key: "短选项" for key in "ABCD"})
        second = self.publication(kind="single_choice", options={key: "短选项" for key in "ABCD"})
        options = {"document": "questions", "option_layout": "four", "option_overrides": {str(second.id): "two"}}
        result = self.post(self.payload([first, second], print_options=options))
        tables = document_xml(result.content).findall(".//w:tbl", NS)
        self.assertEqual([len(table.findall("w:tblGrid/w:gridCol", NS)) for table in tables], [4, 2])
        first.content["options"] = {key: "中文测试字符" for key in "ABCD"}  # six CJK characters
        first.save(update_fields=["content"])
        columns = []
        for size in (12, 16):
            result = self.post(self.payload([first], print_options={"document": "questions", "option_layout": "four", "font_size": size}))
            table = document_xml(result.content).find(".//w:tbl", NS)
            columns.append(len(table.findall("w:tblGrid/w:gridCol", NS)))
            self.assertIn(f'w:val="{size * 2}"'.encode(), etree.tostring(document_xml(result.content)))
        self.assertEqual(columns, [4, 2])

    def test_five_options_and_wide_forced_four_never_drop_or_shrink_content(self):
        pub = self.publication(kind="single_choice", options={key: "短选项" for key in "ABCDE"})
        result = self.post(self.payload([pub], print_options={"document": "questions", "option_layout": "four"}))
        xml = document_xml(result.content)
        table = xml.find(".//w:tbl", NS)
        self.assertEqual(len(table.findall("w:tblGrid/w:gridCol", NS)), 2)
        self.assertIn("E. 短选项", "".join(table.itertext()))
        pub.content["options"] = {key: "长选项" * 30 for key in "ABCD"}
        pub.save(update_fields=["content"])
        result = self.post(self.payload([pub], print_options={"document": "questions", "option_layout": "four", "font_size": 16}))
        xml = document_xml(result.content)
        self.assertIsNone(xml.find(".//w:tbl", NS))
        for key in "ABCD":
            self.assertIn(key + ". " + "长选项" * 30, "".join(xml.itertext()))

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
        answer_index = next(index for index, paragraph in enumerate(paragraphs) if "参考答案与解析" in "".join(paragraph.itertext()))
        self.assertFalse(any(paragraph.find("w:pPr/w:pageBreakBefore", NS) is not None for paragraph in paragraphs[answer_index:]))

    def test_layout_hints_for_unselected_ids_are_rejected_by_export(self):
        pub = self.publication()
        for hints in ({"option_overrides": {str(uuid.uuid4()): "four"}}, {"question_breaks": [str(uuid.uuid4())]}):
            response = self.post(self.payload([pub], print_options=hints))
            self.assertEqual(response.status_code, 400)
            self.assertIn("当前选中", response.json()["error"])

    def test_mathml_annotation_mismatch_external_entities_and_unsupported_nodes_fail(self):
        value = math_field("x", '<mi>x</mi>')
        for change in (lambda raw: raw.replace(">x</annotation>", ">z</annotation>"),
                       lambda raw: '<!DOCTYPE math [<!ENTITY x SYSTEM "file:///private">]>' + raw,
                       lambda raw: raw.replace("<mi>x</mi>", '<unsupported>x</unsupported>'),
                       lambda raw: raw.replace('<mi>x</mi>', '<mi>x</mi><mi href="https://invalid">y</mi>')):
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

    def test_html_merged_table_has_native_span_structure(self):
        raw = '<table><tr><th rowspan="2">甲</th><th>乙</th></tr><tr><td>&amp; 丙</td></tr></table>'
        pub = self.publication(stem=raw)
        rows = export._table_source(raw, "合成表格")
        for row in rows:
            for cell in row:
                cell["segments"] = field(cell["source"])["blocks"][0]["segments"]
        payload = self.payload([pub])
        payload["rendered_fields"][str(pub.id)]["stem"] = {"source": raw, "blocks": [{"type": "table", "start": 0, "end": units(raw), "rows": rows}]}
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        xml = document_xml(result.content)
        self.assertTrue(xml.findall(".//w:vMerge", NS))
        self.assertIn("& 丙", "".join(xml.itertext()))

    def test_table_span_out_of_bounds_and_embedded_image_are_rejected(self):
        for raw in ('<table><tr><td rowspan="3">甲</td></tr></table>',
                    '<table><tr><td><img src="https://invalid">甲</td></tr></table>'):
            pub = self.publication(stem=raw)
            try:
                rows = export._table_source(raw, "合成表格")
            except export.ExportError:
                continue
            for row in rows:
                for cell in row:
                    cell["segments"] = field(cell["source"])["blocks"][0]["segments"]
            payload = self.payload([pub])
            payload["rendered_fields"][str(pub.id)]["stem"] = {"source": raw, "blocks": [{"type": "table", "start": 0, "end": units(raw), "rows": rows}]}
            self.assertEqual(self.post(payload).status_code, 400)

    def test_missing_corrupt_unknown_slot_and_unsafe_image_path_abort(self):
        pub = self.publication()
        target = self.figure(pub)
        for mutation in (lambda: target.unlink(), lambda: target.write_bytes(b"bad png"),
                         lambda: pub.content["figures"][0].update(slot="unknown"),
                         lambda: pub.content["figures"][0].update(file="../private.png")):
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
        pub = self.publication(kind="single_choice", options={key: "离线选项" for key in "ABCD"})
        result = self.post(self.payload([pub], print_options={"document": "questions"}))
        xml = document_xml(result.content)
        table = xml.find(".//w:tbl", NS)
        self.assertIsNotNone(table)
        self.assertEqual(len(table.findall("w:tr", NS)), 1)
        self.assertEqual(len(table.findall("w:tblGrid/w:gridCol", NS)), 4)
        self.assertEqual(table.find("w:tblPr/w:tblBorders/w:top", NS).get(f'{{{NS["w"]}}}val'), "nil")
        for paragraph in table.findall("w:tr", NS)[-1].findall(".//w:p", NS):
            self.assertEqual(paragraph.find("w:pPr/w:keepNext", NS).get(f'{{{NS["w"]}}}val'), "0")
        for mutation in (lambda: pub.content["options"].update(A="长" * 37),
                         lambda: pub.content["options"].update(A="第一行\n第二行"),
                         lambda: self.figure(pub, slot="A")):
            pub.content["options"]["A"] = "离线选项"
            mutation()
            pub.save(update_fields=["content"])
            response = self.post(self.payload([pub], print_options={"document": "questions"}))
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(document_xml(response.content).find(".//w:tbl", NS))

    def test_explicit_picture_columns_retain_all_labels_images_and_natural_width(self):
        pub = self.publication(kind="single_choice", options={key: "" for key in "ABCD"})
        folder = self.root / "library" / str(pub.id)
        folder.mkdir(parents=True)
        pub.content["figures"] = []
        for index, key in enumerate("ABCD", 1):
            target = folder / (f"figure-{index}.png")
            Image.new("RGB", (200, 90), "red").save(target)
            pub.content["figures"].append({"slot": key, "file": target.name, "page_idx": 0, "bbox": [0, 0, 100, 100]})
        pub.content_hash = library.content_hash(pub.content)
        pub.save(update_fields=["content", "content_hash"])
        for mode, columns in (("four", 4), ("two", 2), ("vertical", 0), ("auto", 0)):
            with self.subTest(mode=mode):
                response = self.post(self.payload([pub], print_options={"document": "questions", "option_layout": mode}))
                self.assertEqual(response.status_code, 200, response.content[:100])
                tree = document_xml(response.content)
                table = tree.find(".//w:tbl", NS)
                self.assertEqual(len(table.findall("w:tblGrid/w:gridCol", NS)) if table is not None else 0, columns)
                self.assertEqual(len(tree.findall(".//a:blip", NS)), 4)
                for letter in "ABCD":
                    self.assertIn(letter + ".", "".join(tree.itertext()))
                extents = tree.xpath("//*[local-name()='extent' and namespace-uri()='http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing']/@cx")
                self.assertTrue(all(abs(int(width) / 36000 - 200 * 25.4 / 150) < .01 for width in extents))

        # A wide image downgrades the requested grid and is never shrunk just
        # to preserve the number of columns. The transport explains the change.
        Image.new("RGB", (600, 90), "red").save(folder / "figure-1.png")
        response = self.post(self.payload([pub], print_options={"document": "questions", "option_layout": "four"}))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(document_xml(response.content).find(".//w:tbl", NS))
        self.assertIn("X-QB-Layout-Warning", response)

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
        for mutation in (lambda: PublishedQuestion.objects.filter(pk=pub.id).update(status="withdrawn"),
                         lambda: PublishedQuestion.objects.filter(pk=pub.id).update(content={**pub.content, "stem": "另一版"})):
            pub.status = "published"
            pub.save(update_fields=["content", "status"])
            def changed(*args, **kwargs):
                data = original(*args, **kwargs)
                mutation()
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
        self.assertEqual(self.client.post("/api/library/export-docx", json.dumps(payload), content_type="application/json").status_code, 403)
        self.assertEqual(self.client.post("/api/library/export-docx", json.dumps(payload), content_type="text/plain", HTTP_X_QB_REQUEST="1").status_code, 415)
        self.assertEqual(self.post(payload, REMOTE_ADDR="192.0.2.1").status_code, 403)
        self.assertEqual(self.post(payload, REMOTE_ADDR="::1").status_code, 200)
        self.assertEqual(export.safe_filename("CON"), "练习-CON")
        self.assertEqual(export.safe_filename("练习/一?"), "练习_一_")
        for change in ({"format": []}, {"ids": []}, {"ids": [str(pub.id), str(pub.id)]},
                       {"print_options": {"font_size": True}}, {"print_options": {"student_info": 1}},
                       {"private_key": "synthetic"}):
            self.assertEqual(self.post({**payload, **change}).status_code, 400)
        response = self.client.post("/api/library/export-docx", '"' + 'x' * (export.MAX_BODY + 1) + '"',
                                    content_type="application/json", HTTP_X_QB_REQUEST="1")
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
        for bad in ({"font_size": 13}, {"font_size": True}, {"document": "other"}, {"answer_space": "other"}, {"origin": 1}):
            with self.assertRaises(library_drafts.DraftError):
                library_drafts._print_options(bad)

    def test_old_schema1_is_read_in_memory_without_rewriting_file(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(DATA_ROOT=Path(directory)):
            record = {"id": str(uuid.uuid4()), "title": "旧草稿", "ids": [],
                      "print_options": {"answers": False, "origin": True, "ai_answers": False},
                      "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00", "revision": 1}
            data = json.dumps({"schema": 1, "drafts": [record]}, ensure_ascii=False).encode("utf-8")
            target = Path(directory) / "library-drafts.json"
            target.write_bytes(data)
            result = library_drafts._read()["drafts"][0]
            self.assertEqual(result["print_options"]["document"], "questions")
            self.assertEqual(result["print_options"]["font_size"], 12)
            self.assertEqual(target.read_bytes(), data)

    def test_new_print_options_roundtrip_and_partial_update_keep_layout(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(DATA_ROOT=Path(directory)):
            first = library_drafts._save({"title": "新版草稿", "ids": [], "print_options": {
                "document": "answers", "font_size": 16, "answer_space": "large", "student_info": False}})
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
        normalized = library_drafts._print_options({"option_overrides": {key.upper(): "two"}, "question_breaks": [key.upper()]}, ids=[key])
        self.assertEqual(normalized["option_overrides"], {key: "two"})
        self.assertEqual(normalized["question_breaks"], [key])
        for invalid in ({"pagination": "other"}, {"option_layout": False}, {"option_overrides": []},
                        {"option_overrides": {key: False}}, {"option_overrides": {key: "three"}},
                        {"option_overrides": {other: "four"}}, {"option_overrides": {key: "four", key.upper(): "two"}},
                        {"question_breaks": key}, {"question_breaks": [other]}, {"question_breaks": [key, key]},
                        {"question_breaks": [str(uuid.uuid4()) for _ in range(501)]}):
            with self.assertRaises(library_drafts.DraftError):
                library_drafts._print_options(invalid, ids=[key])

    def test_layout_hints_roundtrip_and_removed_question_hints_are_cleaned_atomically(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(DATA_ROOT=Path(directory)):
            first, second = str(uuid.uuid4()), str(uuid.uuid4())
            record = library_drafts._save({"ids": [first, second], "print_options": {"pagination": "keep", "option_layout": "four",
                "option_overrides": {second: "two"}, "question_breaks": [second]}})
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

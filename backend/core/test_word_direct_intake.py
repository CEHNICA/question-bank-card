"""Stage 1 tests: Word (.docx / .doc) dual-track direct reading, OMML/MathType, and answer splitting."""

from __future__ import annotations

import io
import json
import struct
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from docx import Document
from docx.oxml import parse_xml
from PIL import Image

from . import library, library_solutions, views, word, word_extract, word_mtef, word_omml
from .models import LibrarySolution, Paper, PublishedQuestion, Question


def _png_bytes(color: tuple[int, int, int] = (20, 120, 220), size: tuple[int, int] = (96, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _build_ole2_single_stream(stream_name: str, payload: bytes) -> bytes:
    """Build a minimal valid 512-byte-sector MS-CFB (OLE2) container with one stream (>= 4096 padded or regular sector)."""
    # Force regular FAT stream (>= mini cutoff 4096 or set mini_cutoff=0) by setting mini_cutoff = 0!
    sector_size = 512
    num_stream_sects = max(1, (len(payload) + sector_size - 1) // sector_size)
    # Sector layout: 0 = FAT, 1 = Directory, 2..(2+num_stream_sects-1) = stream data
    header = bytearray(512)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<H", header, 0x1A, 3)  # DLL version 3
    struct.pack_into("<H", header, 0x1C, 0xFFFE)  # little-endian
    struct.pack_into("<H", header, 0x1E, 9)  # 512-byte sectors
    struct.pack_into("<H", header, 0x20, 6)  # 64-byte mini sectors
    struct.pack_into("<I", header, 0x2C, 1)  # 1 FAT sector
    struct.pack_into("<I", header, 0x30, 1)  # first dir sector = 1
    struct.pack_into("<I", header, 0x38, 0)  # mini_cutoff = 0 -> all streams in main FAT
    struct.pack_into("<I", header, 0x3C, 0xFFFFFFFE)  # no minifat
    struct.pack_into("<I", header, 0x40, 0)
    struct.pack_into("<I", header, 0x44, 0xFFFFFFFE)  # no extra difat
    struct.pack_into("<I", header, 0x48, 0)
    difat = [0] + [0xFFFFFFFF] * 108
    struct.pack_into("<109I", header, 0x4C, *difat)

    fat = [0xFFFFFFFF] * 128
    fat[0] = 0xFFFFFFFD  # FATSECT
    fat[1] = 0xFFFFFFFE  # Dir end
    for i in range(num_stream_sects):
        sec = 2 + i
        fat[sec] = (sec + 1) if i + 1 < num_stream_sects else 0xFFFFFFFE
    fat_bytes = struct.pack("<128I", *fat)

    dir_sec = bytearray(512)
    # Entry 0: Root Entry
    root_name = "Root Entry\x00".encode("utf-16-le")
    dir_sec[0 : len(root_name)] = root_name
    struct.pack_into("<H", dir_sec, 0x40, len(root_name))
    dir_sec[0x42] = 5  # root storage
    dir_sec[0x43] = 1
    struct.pack_into("<I", dir_sec, 0x44, 0xFFFFFFFF)
    struct.pack_into("<I", dir_sec, 0x48, 0xFFFFFFFF)
    struct.pack_into("<I", dir_sec, 0x4C, 1)  # child = entry 1
    struct.pack_into("<I", dir_sec, 0x74, 0xFFFFFFFE)
    struct.pack_into("<I", dir_sec, 0x78, 0)

    # Entry 1: Stream
    ent = bytearray(128)
    sname = (stream_name + "\x00").encode("utf-16-le")
    ent[0 : len(sname)] = sname
    struct.pack_into("<H", ent, 0x40, len(sname))
    ent[0x42] = 2  # stream
    ent[0x43] = 1
    struct.pack_into("<I", ent, 0x44, 0xFFFFFFFF)
    struct.pack_into("<I", ent, 0x48, 0xFFFFFFFF)
    struct.pack_into("<I", ent, 0x4C, 0xFFFFFFFF)
    struct.pack_into("<I", ent, 0x74, 2)  # start sector = 2
    struct.pack_into("<I", ent, 0x78, len(payload))
    dir_sec[128:256] = ent

    stream_padded = payload + b"\x00" * (num_stream_sects * sector_size - len(payload))
    return bytes(header) + fat_bytes + bytes(dir_sec) + stream_padded


class OmmlAndMtefUnitTests(SimpleTestCase):
    def test_omml_converts_fractions_radicals_cases_and_vectors(self):
        ns = f'xmlns:m="{word_omml.OMML_NS}"'
        xml = (
            f"<m:oMath {ns}>"
            "<m:f><m:num><m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup></m:num>"
            "<m:den><m:rad><m:radPr><m:degHide m:val='1'/></m:radPr><m:deg/><m:e><m:r><m:t>α+1</m:t></m:r></m:e></m:rad></m:den></m:f>"
            "<m:r><m:t>≤</m:t></m:r>"
            "<m:acc><m:accPr><m:chr m:val='→'/></m:accPr><m:e><m:r><m:t>AB</m:t></m:r></m:e></m:acc>"
            "</m:oMath>"
        )
        latex, warns = word_omml.omml_xml_to_latex(xml)
        self.assertEqual(warns, [])
        self.assertIn(r"\frac{x^{2}}{\sqrt{\alpha+1}}", latex)
        self.assertIn(r"\leqslant", latex)
        self.assertIn(r"\overrightarrow{AB}", latex)

    def test_omml_converts_piecewise_cases_and_arc_groupchr(self):
        ns = f'xmlns:m="{word_omml.OMML_NS}"'
        xml = (
            f"<m:oMath {ns}>"
            "<m:d><m:dPr><m:begChr m:val='{'/><m:endChr m:val=''/></m:dPr>"
            "<m:e><m:eqArr>"
            "<m:e><m:r><m:t>x+1, x≥0</m:t></m:r></m:e>"
            "<m:e><m:r><m:t>-x, x&lt;0</m:t></m:r></m:e>"
            "</m:eqArr></m:e></m:d>"
            "<m:groupChr><m:groupChrPr><m:chr m:val='︵'/><m:pos m:val='top'/></m:groupChrPr>"
            "<m:e><m:r><m:t>AB</m:t></m:r></m:e></m:groupChr>"
            "</m:oMath>"
        )
        latex, _ = word_omml.omml_xml_to_latex(xml)
        self.assertIn(r"\begin{cases}", latex)
        self.assertIn(r"\overgroup{AB}", latex)

    def test_mtef_v5_ole_equation_converts_to_latex(self):
        # MTEF v5: \frac{\alpha}{\sqrt{2}}
        # Header: v=5, platform=1, product=0, ver=6, sub=0, "DSMT4\0", inline=0
        mtef = bytearray([5, 1, 0, 6, 0]) + b"DSMT4\x00" + bytearray([0])
        # LINE(1, opts=0) -> TMPL(3, opts=0, selector=11 fraction, var=0, topts=0)
        #   slot1 LINE(1, 0): CHAR(2, 0, typeface=4+128=132 Greek, mtcode=0x03B1 'α') END(0)
        #   slot2 LINE(1, 0): TMPL(3, 0, selector=10 radical, var=0, topts=0)
        #     slot1 LINE(1, 0): CHAR(2, 0, typeface=3+128=131, mtcode=ord('2')) END(0)
        #   END(0)  END(0)
        # END(0)  END(0)
        mtef += bytes([1, 0, 3, 0, 11, 0, 0])
        mtef += bytes([1, 0, 2, 0, 132]) + struct.pack("<H", 0x03B1) + bytes([0])
        mtef += bytes([1, 0, 3, 0, 10, 0, 0, 1, 0, 2, 0, 131]) + struct.pack("<H", ord("2")) + bytes([0, 0, 0])
        mtef += bytes([0, 0, 0])
        eq_header = struct.pack("<H", 28) + b"\x00" * 26
        ole = _build_ole2_single_stream("Equation Native", eq_header + bytes(mtef))
        latex = word_mtef.ole_equation_to_latex(ole)
        self.assertEqual(latex, r"\frac{\alpha}{\sqrt{2}}")


class WordDirectIntakeTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        config = override_settings(DATA_ROOT=self.folder)
        config.enable()
        self.addCleanup(config.disable)
        self.factory = RequestFactory()

    def _build_rich_exam_docx(self, path: Path) -> tuple[bytes, bytes]:
        """Build a synthetic multi-question .docx with OMML, option table, stem figure, inline answer, and answer chapter with solution figure."""
        doc = Document()
        doc.add_paragraph("2026 学年高一数学周测卷")
        doc.add_paragraph("注意事项：1. 答题前请填写姓名与班级，本试卷共 150 分，考试时间 120 分钟。")

        doc.add_paragraph("一、选择题（本题共 2 小题，每小题 5 分）")
        p1 = doc.add_paragraph("1. （2025·海淀期中）已知集合 ")
        p1._p.append(parse_xml(f'<m:oMath xmlns:m="{word_omml.OMML_NS}"><m:r><m:t>A={{1,2}}</m:t></m:r></m:oMath>'))
        p1.add_run("，则子集个数为（ ）")
        # 1x4 option layout table
        tbl = doc.add_table(rows=1, cols=4)
        for idx, txt in enumerate(("A. 1", "B. 2", "C. 3", "D. 4")):
            tbl.rows[0].cells[idx].text = txt
        doc.add_paragraph("【答案】D【分析】集合含 2 个元素。【详解】子集个数为 $2^2=4$。")

        p2 = doc.add_paragraph("2. 如图所示，在 △ABC 中，下列结论正确的是（ ）")
        q2_fig = _png_bytes((200, 40, 40), (120, 80))
        doc.add_paragraph().add_run().add_picture(io.BytesIO(q2_fig))
        doc.add_paragraph("A. AB=AC\tB. BC=AC\tC. ∠A=90°\tD. ∠B=60°")

        doc.add_paragraph("二、填空题")
        p3 = doc.add_paragraph("3. 计算：")
        p3._p.append(
            parse_xml(
                f'<m:oMath xmlns:m="{word_omml.OMML_NS}">'
                "<m:f><m:num><m:r><m:t>1</m:t></m:r></m:num><m:den><m:r><m:t>2</m:t></m:r></m:den></m:f>"
                "</m:oMath>"
            )
        )
        r_blank = p3.add_run("      ")
        r_blank.underline = True
        p3.add_run("。")

        doc.add_paragraph("三、解答题")
        doc.add_paragraph("4. 已知函数 $f(x)=x^2$。\n(1) 求 $f(2)$；\n(2) 求单调递增区间。")

        # End-of-document answer chapter
        doc.add_paragraph("参考答案与解析")
        doc.add_paragraph("2. C\n【解析】由原图几何关系得 ∠A=90°。")
        doc.add_paragraph("3. 【答案】$\\frac{1}{2}$")
        doc.add_paragraph("4. 解：(1) $f(2)=4$；(2) 单调递增区间为 $[0,+\\infty)$。")
        sol_fig = _png_bytes((30, 160, 80), (140, 90))
        doc.add_paragraph().add_run().add_picture(io.BytesIO(sol_fig))

        doc.save(path)
        return q2_fig, sol_fig

    def test_docx_direct_intake_zero_office_fallback_and_publish_with_solution_figure(self):
        docx_path = self.folder / "exam.docx"
        q2_fig_bytes, sol_fig_bytes = self._build_rich_exam_docx(docx_path)

        upload = io.BytesIO(docx_path.read_bytes())
        upload.name = "周测卷.docx"
        request = self.factory.post("/api/papers", {"file": upload}, HTTP_X_QB_REQUEST="1")

        # Simulate a machine with NO Word, NO WPS, and NO cloud/LLM credentials
        with (
            mock.patch("core.word._com_convert", side_effect=RuntimeError("no office installed")),
            mock.patch("core.views.readers.configured", return_value=False),
            mock.patch("core.views._reading_ready", return_value=False),
            mock.patch("core.pipeline.request_extract_file_from_pool") as cloud_mock,
        ):
            response = views.papers(request)

        self.assertEqual(response.status_code, 201, response.content)
        cloud_mock.assert_not_called()

        paper = Paper.objects.get()
        self.assertEqual(paper.status, Paper.Status.READY)
        self.assertTrue(paper.processing_plan.get("word_direct"))
        self.assertEqual(paper.processing_plan.get("evidence_source"), "html_fallback")
        self.assertTrue(Path(paper.render_path).is_file())
        self.assertGreaterEqual(len(paper.pages), 1)

        cards = list(paper.questions.order_by("number"))
        self.assertEqual([c.number for c in cards], [1, 2, 3, 4])

        # Q1: OMML + origin split + option table + inline answer/analysis stripped from stem
        q1, q2, q3, q4 = cards
        self.assertEqual(q1.question_type, "single_choice")
        self.assertEqual(q1.origin, "2025·海淀期中")
        self.assertIn(r"A=\{1,2\}", q1.stem)
        self.assertNotIn("【答案】", q1.stem)
        self.assertEqual(q1.options["D"], "4")
        self.assertEqual(q1.answer, "D")
        self.assertIn("【分析】", q1.analysis)
        self.assertIn("【详解】", q1.analysis)
        self.assertEqual(q1.state, Question.State.GREEN, q1.flags)
        self.assertFalse(q1.approved, "必须先进录题审核页等人眼确认，绝不自动通过")

        # Q2: stem figure bound losslessly, chapter answer C & analysis attached
        self.assertEqual(len(q2.figures), 1)
        self.assertEqual(q2.figures[0]["slot"], "stem")
        self.assertEqual(q2.answer, "C")
        self.assertIn("几何关系", q2.analysis)
        self.assertEqual(q2.state, Question.State.GREEN, q2.flags)

        # Q3: underlined whitespace converted to ______ fill_blank + OMML \frac{1}{2}
        self.assertEqual(q3.question_type, "fill_blank")
        self.assertIn("______", q3.stem)
        self.assertIn(r"\frac{1}{2}", q3.stem)
        self.assertEqual(q3.answer, r"$\frac{1}{2}$")
        self.assertEqual(q3.state, Question.State.GREEN, q3.flags)

        # Q4: free_response worked solution + solution figure NOT mixed into student stem figures!
        self.assertEqual(q4.question_type, "free_response")
        self.assertEqual(q4.figures, [])
        self.assertIn("单调递增区间", q4.analysis)
        q4_json = views.question_json(q4)
        self.assertEqual(q4_json["solution_figure_count"], 1)

        # Approve Q4 on review page -> publishes PublishedQuestion + materializes LibrarySolution with solution image!
        approve_req = self.factory.post(
            "/", json.dumps({"approved": True}), content_type="application/json", HTTP_X_QB_REQUEST="1"
        )
        approve_res = views.question_action(approve_req, q4.pk, "approve")
        self.assertEqual(approve_res.status_code, 200, approve_res.content)
        pub4 = PublishedQuestion.objects.get(question=q4, status=PublishedQuestion.Status.PUBLISHED)
        sol_id = (pub4.extras or {}).get("solution_id")
        self.assertTrue(sol_id, "解析配图在通过入库时应自动生成正式题库 LibrarySolution")
        sol_row = LibrarySolution.objects.get(pk=sol_id)
        self.assertEqual(len(sol_row.figures), 1)
        captured_imgs = library_solutions.capture_images(sol_row)
        self.assertEqual(len(captured_imgs), 1)
        with Image.open(io.BytesIO(captured_imgs[0]["bytes"])) as im:
            self.assertEqual(im.size, (140, 90))

    def test_vector_drawing_ole_fallback_and_answer_mismatch_mark_yellow_flags(self):
        docx_path = self.folder / "edge_cases.docx"
        doc = Document()
        doc.add_paragraph("一、解答题")
        # Q1: Word vector line-art geometry shape (v:line inside w:pict)
        p1 = doc.add_paragraph("1. 如图，用线段拼成的几何图形中，求 x。")
        p1._p.append(
            parse_xml(
                '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:v="urn:schemas-microsoft-com:vml">'
                '<w:pict><v:line from="0,0" to="50pt,50pt"/></w:pict></w:r>'
            )
        )
        # Q2: Ancient unsupported OLE equation with preview image preserved
        p2 = doc.add_paragraph("2. 化简古董公式：")
        doc.save(docx_path)

        # Inject a fake ancient OLE object + preview PNG into Q2 via zipfile modification
        preview_png = _png_bytes((90, 60, 180), (60, 30))
        patched_path = self.folder / "edge_cases_patched.docx"
        with zipfile.ZipFile(docx_path, "r") as zin, zipfile.ZipFile(patched_path, "w") as zout:
            for item in zin.infolist():
                raw = zin.read(item.filename)
                if item.filename == "word/_rels/document.xml.rels":
                    rel_str = raw.decode("utf-8").replace(
                        "</Relationships>",
                        '<Relationship Id="rIdOle99" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="embeddings/ancient.bin"/>'
                        '<Relationship Id="rIdPrev99" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/ole_prev.png"/>'
                        "</Relationships>",
                    )
                    raw = rel_str.encode("utf-8")
                elif item.filename == "word/document.xml":
                    doc_str = raw.decode("utf-8")
                    ole_snippet = (
                        '<w:r xmlns:v="urn:schemas-microsoft-com:vml" '
                        'xmlns:o="urn:schemas-microsoft-com:office:office" '
                        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                        '<w:object><v:shape><v:imagedata r:id="rIdPrev99"/></v:shape>'
                        '<o:OLEObject Type="Embed" ProgID="AncientEquation.1" r:id="rIdOle99"/></w:object></w:r>'
                        "</w:p>"
                        "<w:p><w:r><w:t>参考答案</w:t></w:r></w:p>"
                        "<w:p><w:r><w:t>1. x=5</w:t></w:r></w:p>"
                        "<w:p><w:r><w:t>9. 多余的题号答案</w:t></w:r></w:p>"
                    )
                    idx = doc_str.rfind("</w:p>")
                    doc_str = doc_str[:idx] + ole_snippet + doc_str[idx + len("</w:p>") :]
                    raw = doc_str.encode("utf-8")
                zout.writestr(item, raw)
            zout.writestr("word/embeddings/ancient.bin", b"NOT_A_VALID_MTEF_STREAM")
            zout.writestr("word/media/ole_prev.png", preview_png)

        upload = io.BytesIO(patched_path.read_bytes())
        upload.name = "疑难测试.docx"
        request = self.factory.post("/api/papers", {"file": upload}, HTTP_X_QB_REQUEST="1")
        with mock.patch("core.word._com_convert", side_effect=RuntimeError("no office")):
            response = views.papers(request)
        self.assertEqual(response.status_code, 201, response.content)

        paper = Paper.objects.get()
        self.assertTrue(any("第 9 题" in w for w in (paper.processing_plan.get("warnings") or [])))
        q1, q2 = list(paper.questions.order_by("number"))

        # Q1: vector drawing flagged yellow + blocked missing figure because stem says "如图"
        self.assertEqual(q1.state, Question.State.YELLOW)
        self.assertIn(word_extract.FLAG_VECTOR_DRAWING, q1.flags)
        self.assertEqual(q1.answer, "x=5")

        # Q2: ancient OLE preserved preview figure + flagged yellow + flagged missing chapter answer!
        self.assertEqual(q2.state, Question.State.YELLOW)
        self.assertIn(word_extract.FLAG_OLE_EQUATION_FALLBACK, q2.flags)
        self.assertEqual(len(q2.figures), 1)
        self.assertTrue(any("未匹配到第 2 题答案" in f for f in q2.flags), q2.flags)

    def test_legacy_doc_upload_converts_via_com_or_reports_actionable_message(self):
        real_docx = self.folder / "template.docx"
        doc = Document()
        doc.add_paragraph("1. 求 $1+1$ 的值：______。【答案】2")
        doc.save(real_docx)

        # Case A: COM available -> converts .doc to .docx + .pdf in one pass
        fake_doc_bytes = b"\xd0\xcf\x11\xe0legacy_binary_doc"
        upload_ok = io.BytesIO(fake_doc_bytes)
        upload_ok.name = "老试卷.doc"

        def fake_com(source: Path, *, pdf_target: Path | None = None, docx_target: Path | None = None) -> str:
            if docx_target is not None:
                docx_target.write_bytes(real_docx.read_bytes())
            if pdf_target is not None:
                word.render_docx_fallback_pdf(real_docx, pdf_target)
            return "wps"

        req_ok = self.factory.post("/api/papers", {"file": upload_ok}, HTTP_X_QB_REQUEST="1")
        with mock.patch("core.word._com_convert", side_effect=fake_com):
            res_ok = views.papers(req_ok)
        self.assertEqual(res_ok.status_code, 201, res_ok.content)
        paper_ok = Paper.objects.get(filename="老试卷.doc")
        self.assertEqual(paper_ok.status, Paper.Status.READY)
        self.assertEqual(paper_ok.processing_plan.get("evidence_app"), "wps")
        q_ok = paper_ok.questions.get()
        self.assertEqual(q_ok.answer, "2")

        # Case B: COM unavailable -> .doc fails gracefully with clear teacher-facing message
        upload_no_office = io.BytesIO(b"\xd0\xcf\x11\xe0another_legacy_doc")
        upload_no_office.name = "无软件老卷.doc"
        req_fail = self.factory.post("/api/papers", {"file": upload_no_office}, HTTP_X_QB_REQUEST="1")
        with mock.patch("core.word._com_convert", side_effect=RuntimeError("no office")):
            res_fail = views.papers(req_fail)
        self.assertEqual(res_fail.status_code, 201, res_fail.content)
        paper_fail = Paper.objects.get(filename="无软件老卷.doc")
        self.assertEqual(paper_fail.status, Paper.Status.FAILED)
        self.assertIn("老版 .doc", paper_fail.error)

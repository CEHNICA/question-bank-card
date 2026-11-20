"""切割原图题卡直接入库、MinerU 只切题模式、Eagle 筛选/编辑/OneNote 与按需转文字测试。"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from PIL import Image

from core import library, library_drafts, library_export, library_onenote, library_pdf, pipeline
from core.models import Block, LibraryTag, LibraryTagAssignment, Paper, PublishedQuestion, Question, QuestionGroup


def _make_pdf_bytes(text: str = "1. Test question") -> bytes:
    """生成一份最小合法单页 PDF 用于隔离测试。"""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), text)
    data = doc.tobytes()
    doc.close()
    return data


def _make_png_bytes(color: tuple[int, int, int] = (240, 248, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (240, 90), color).save(buf, format="PNG")
    return buf.getvalue()


class CutImageLibraryFlowTests(TestCase):
    def setUp(self):
        self.pdf_bytes = _make_pdf_bytes()
        self.paper = Paper.objects.create(
            filename="cut-flow.pdf",
            kind="pdf",
            sha256=hashlib.sha256(self.pdf_bytes).hexdigest(),
            source_path="",
            pages=[{"page_idx": 0, "width": 595, "height": 842}],
            processing_plan={"schema": 1, "mode": "manual", "revision": 1},
            status=Paper.Status.READY,
        )
        folder = Path(settings.DATA_ROOT) / str(self.paper.pk)
        folder.mkdir(parents=True, exist_ok=True)
        source_file = folder / "source.pdf"
        source_file.write_bytes(self.pdf_bytes)
        self.paper.source_path = str(source_file)
        self.paper.save(update_fields=["source_path"])
        self.group = QuestionGroup.objects.create(
            paper=self.paper,
            title="cut-flow.pdf",
            sequence=0,
            page_start=1,
            page_end=1,
            metadata={"pages": [0], "source": "manual"},
        )

    def _post(self, url: str, payload: dict | None = None):
        return self.client.post(
            url, data=json.dumps(payload or {}), content_type="application/json", HTTP_X_QB_REQUEST="1"
        )

    def test_publish_cut_images_allows_unknown_type_while_approve_green_keeps_text_guard(self):
        cut_ready = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=1,
            question_type="unknown",
            regions=[{"page_idx": 0, "bbox": [50, 50, 500, 300]}],
            regions_auto=[{"page_idx": 0, "bbox": [50, 50, 500, 300]}],
            body_mode="source_image",
            processing_mode="manual",
            state=Question.State.YELLOW,
        )
        cut_pending = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=2,
            question_type="unknown",
            regions=[{"page_idx": 0, "bbox": [50, 320, 500, 500]}],
            regions_auto=[{"page_idx": 0, "bbox": [50, 320, 500, 500]}],
            body_mode="source_image",
            processing_mode="manual",
            ocr_pending=True,
            state=Question.State.YELLOW,
        )
        text_unknown = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=3,
            question_type="unknown",
            regions=[{"page_idx": 0, "bbox": [50, 520, 500, 700]}],
            regions_auto=[{"page_idx": 0, "bbox": [50, 520, 500, 700]}],
            body_mode="text",
            processing_mode="auto",
            stem="已知 $x>0$，求 $x+1/x$ 的最小值。",
            state=Question.State.GREEN,
        )

        resp = self._post(f"/api/papers/{self.paper.pk}/publish-cut-images", {"by": "human"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["published"], 1)
        self.assertEqual(len(data["skipped"]), 1)
        self.assertEqual(data["skipped"][0]["number"], cut_pending.number)

        cut_ready.refresh_from_db()
        text_unknown.refresh_from_db()
        self.assertTrue(cut_ready.approved)
        self.assertFalse(text_unknown.approved)

        pub = PublishedQuestion.objects.get(question=cut_ready, status=PublishedQuestion.Status.PUBLISHED)
        self.assertEqual(pub.question_type, "unknown")
        self.assertEqual(pub.content.get("body_mode"), "source_image")

        # 现有绿卡批量通过逻辑仍拦截「题型未定」的文字题
        green_resp = self._post(f"/api/papers/{self.paper.pk}/approve-green", {"by": "human"})
        self.assertEqual(green_resp.status_code, 200)
        self.assertEqual(green_resp.json()["approved"], 0)
        text_unknown.refresh_from_db()
        self.assertFalse(text_unknown.approved)

    def test_mineru_cut_only_upload_without_reader_key_and_skips_reading(self):
        upload = SimpleUploadedFile(
            "mineru-cut.pdf", _make_pdf_bytes("1. MinerU cut-only question"), content_type="application/pdf"
        )
        with (
            mock.patch("core.views.readers.configured", side_effect=lambda name: name == "mineru"),
            mock.patch("core.views._reading_ready", return_value=False),
        ):
            resp = self.client.post(
                "/api/papers", {"file": upload, "parse_mode": "mineru", "cut_only": "1"}, HTTP_X_QB_REQUEST="1"
            )
        self.assertEqual(resp.status_code, 201)
        paper_id = resp.json()["paper"]["id"]
        paper = Paper.objects.get(pk=paper_id)
        self.assertTrue((paper.processing_plan or {}).get("cut_only"))

        # 模拟 MinerU 解析已完成，直接从 SEGMENTING 走 process_paper
        paper.status = Paper.Status.SEGMENTING
        paper.pages = [{"page_idx": 0, "width": 595, "height": 842}]
        paper.save(update_fields=["status", "pages"])
        Block.objects.create(
            paper=paper,
            seq=1,
            type="text",
            page_idx=0,
            bbox=[50, 80, 500, 180],
            text="1. 已知集合 $A=\\{1,2\\}$，求集合 $A$ 的子集个数。",
        )

        with mock.patch("core.pipeline.read_questions") as mock_read:
            pipeline.process_paper(paper)
        mock_read.assert_not_called()

        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.READY)
        cards = list(paper.questions.all())
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].body_mode, "source_image")
        self.assertEqual(cards[0].processing_mode, "manual")
        self.assertEqual(cards[0].state, Question.State.YELLOW)

    def test_library_browse_filters_editor_and_onenote_for_source_image(self):
        img_q = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=1,
            question_type="unknown",
            regions=[{"page_idx": 0, "bbox": [50, 50, 500, 300]}],
            regions_auto=[{"page_idx": 0, "bbox": [50, 50, 500, 300]}],
            body_mode="source_image",
            processing_mode="manual",
            state=Question.State.YELLOW,
        )
        txt_q = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=2,
            question_type="single_choice",
            regions=[{"page_idx": 0, "bbox": [50, 320, 500, 500]}],
            regions_auto=[{"page_idx": 0, "bbox": [50, 320, 500, 500]}],
            body_mode="text",
            processing_mode="auto",
            stem="文字题题干",
            options={"A": "1", "B": "2", "C": "3", "D": "4"},
            state=Question.State.GREEN,
        )
        now = timezone.now()
        library.approve(img_q, now=now)
        img_q.save()
        pub_img, _ = library.publish(img_q)
        library.approve(txt_q, now=now)
        txt_q.save()
        pub_txt, _ = library.publish(txt_q)

        # 筛选仅原图题 & 题型未定
        r_img = self.client.get("/api/library?body_mode=source_image&type=unknown")
        self.assertEqual(r_img.status_code, 200)
        ids_img = [item["id"] for item in r_img.json()["items"]]
        self.assertEqual(ids_img, [str(pub_img.pk)])
        self.assertEqual(r_img.json()["facets"]["body_modes"]["source_image"], 1)

        r_txt = self.client.get("/api/library?body_mode=text")
        self.assertEqual(r_txt.status_code, 200)
        self.assertEqual([item["id"] for item in r_txt.json()["items"]], [str(pub_txt.pk)])

        # 题库编辑弹窗接口允许原图题空题干修改题型
        edit_resp = self._post(
            f"/api/library/{pub_img.pk}/question",
            {
                "revision": img_q.content_revision,
                "content_hash": pub_img.content_hash,
                "body_mode": "source_image",
                "stem": "",
                "options": {},
                "question_type": "free_response",
            },
        )
        self.assertEqual(edit_resp.status_code, 201)
        updated_pub = edit_resp.json()["publication"]
        self.assertEqual(updated_pub["question_type"], "free_response")
        self.assertEqual(updated_pub["content"]["body_mode"], "source_image")

        # OneNote 渲染原图题直接读取本地裁片，无需无头浏览器
        latest_pub = PublishedQuestion.objects.get(pk=updated_pub["id"])
        png = library_onenote.render_question_image(latest_pub)
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(b"\x89PNG"))


class LibraryOcrUpgradeTests(TransactionTestCase):
    def setUp(self):
        self.pdf_bytes = _make_pdf_bytes("1. OCR upgrade question")
        self.paper = Paper.objects.create(
            filename="ocr-upgrade.pdf",
            kind="pdf",
            sha256=hashlib.sha256(self.pdf_bytes).hexdigest(),
            source_path="",
            pages=[{"page_idx": 0, "width": 595, "height": 842}],
            processing_plan={"schema": 1, "mode": "manual", "revision": 1},
            status=Paper.Status.READY,
        )
        folder = Path(settings.DATA_ROOT) / str(self.paper.pk)
        folder.mkdir(parents=True, exist_ok=True)
        source_file = folder / "source.pdf"
        source_file.write_bytes(self.pdf_bytes)
        self.paper.source_path = str(source_file)
        self.paper.save(update_fields=["source_path"])
        self.group = QuestionGroup.objects.create(
            paper=self.paper,
            title="ocr-upgrade.pdf",
            sequence=0,
            page_start=1,
            page_end=1,
            metadata={"pages": [0], "source": "manual"},
        )

    def test_library_ocr_upgrade_creates_v2_text_and_carries_tags(self):
        img_q = Question.objects.create(
            paper=self.paper,
            group=self.group,
            number=1,
            question_type="unknown",
            regions=[{"page_idx": 0, "bbox": [50, 50, 500, 300]}],
            regions_auto=[{"page_idx": 0, "bbox": [50, 50, 500, 300]}],
            body_mode="source_image",
            processing_mode="manual",
            state=Question.State.YELLOW,
        )
        now = timezone.now()
        library.approve(img_q, now=now)
        img_q.save()
        v1, _ = library.publish(img_q)

        tag = LibraryTag.objects.create(name="三角函数测试标签", kind=LibraryTag.Kind.KNOWLEDGE)
        LibraryTagAssignment.objects.create(publication=v1, tag=tag, source=LibraryTagAssignment.Source.HUMAN)

        with (
            mock.patch("core.views._vision_ready", return_value=True),
            mock.patch("core.views.readers.assistant_mode", return_value=False),
        ):
            upgrade_resp = self.client.post(
                f"/api/library/{v1.pk}/ocr-upgrade", data="{}", content_type="application/json", HTTP_X_QB_REQUEST="1"
            )
        self.assertEqual(upgrade_resp.status_code, 200)

        img_q.refresh_from_db()
        self.assertTrue(img_q.reread_requested)
        self.assertTrue(img_q.ocr_pending)

        fake_result = {
            "stem": "已知 $\\sin\\alpha=\\frac{1}{2}$，求 $\\cos 2\\alpha$。",
            "options": {},
            "question_type": "free_response",
            "state": Question.State.GREEN,
            "flags": [],
            "figures": [],
            "text_source": "single",
        }
        with (
            mock.patch("core.pipeline.read_card", return_value=fake_result),
            mock.patch("core.pipeline.readers.assistant_mode", return_value=False),
            mock.patch("core.pipeline._cut_source_current", return_value=True),
        ):
            pipeline.process_rereads()

        v1.refresh_from_db()
        self.assertEqual(v1.status, PublishedQuestion.Status.SUPERSEDED)
        v2 = PublishedQuestion.objects.get(question=img_q, status=PublishedQuestion.Status.PUBLISHED)
        self.assertEqual(v2.version, 2)
        self.assertEqual(v2.content.get("body_mode"), None)
        self.assertIn("\\sin\\alpha", v2.content.get("stem", ""))
        self.assertEqual(v2.review_source, "ai")
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=v2, tag=tag).exists())
        self.assertIn("三角函数测试标签", v2.tags_text)


class BasketGroupModeExportTests(TestCase):
    def test_basket_order_group_mode_in_drafts_word_and_pdf(self):
        self.assertEqual(library_drafts.PRINT_DEFAULTS["group_mode"], "by_type")
        opts = library_drafts._print_options({"group_mode": "basket_order"})
        self.assertEqual(opts["group_mode"], "basket_order")
        with self.assertRaises(library_drafts.DraftError):
            library_drafts._print_options({"group_mode": "invalid_mode"})

        captured = [
            {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "type": "unknown",
                "where": "第 1 题",
                "content": {"stem": "未定题干", "options": {}, "section": ""},
                "fields": {"stem": [{"type": "paragraph", "segments": [{"type": "text", "raw": "未定题干"}]}]},
                "images": [],
                "selected": {},
                "solution_images": [],
                "ai": False,
            },
            {
                "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "type": "single_choice",
                "where": "第 2 题",
                "content": {"stem": "选择题干", "options": {"A": "1"}, "section": ""},
                "fields": {
                    "stem": [{"type": "paragraph", "segments": [{"type": "text", "raw": "选择题干"}]}],
                    "options.A": [{"type": "paragraph", "segments": [{"type": "text", "raw": "1"}]}],
                },
                "images": [],
                "selected": {},
                "solution_images": [],
                "ai": False,
            },
        ]

        ordered_default = library_export._ordered(captured, "by_type")
        self.assertEqual(
            [(name, [x["id"] for x in group]) for name, group in ordered_default],
            [("选择题", ["bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"]), ("其他", ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"])],
        )

        ordered_basket = library_export._ordered(captured, "basket_order")
        self.assertEqual(
            [(name, [x["id"] for x in group]) for name, group in ordered_basket],
            [(None, ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"])],
        )

        # Word 混排生成：不会出现「一、其他」大题标题，按选题顺序输出 1. 2.
        from docx import Document

        doc_bytes = library_export._document(captured, "混排练习", opts, "questions")
        doc = Document(io.BytesIO(doc_bytes))
        all_text = "\n".join(p.text for p in doc.paragraphs)
        self.assertNotIn("其他", all_text)
        self.assertIn("未定题干", all_text)
        self.assertIn("选择题干", all_text)
        self.assertLess(all_text.index("未定题干"), all_text.index("选择题干"))

        # PDF HTML 文档同步收到 basket_order 且 _section_headings 为空
        self.assertEqual(library_pdf._section_headings(captured, "basket_order"), {})
        html_doc = library_pdf._html_document(captured, "混排练习", opts)
        self.assertIn('"group_mode": "basket_order"', html_doc)
        self.assertIn("o.group_mode==='basket_order'", html_doc)

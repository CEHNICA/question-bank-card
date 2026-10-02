"""Offline crop reading suggestions: exact safe anchors and human-only saving."""

import json
from copy import deepcopy
from unittest import mock

from django.test import Client, SimpleTestCase, TestCase

from . import readers, region_reads
from .models import RegionRead
from .test_v110_types_origin import TempDataMixin


class RegionRecommendationTests(SimpleTestCase):
    def base(self, stem="已知 $x+1=3$，求 $x$ 的值。", **options):
        return {"fields": {"stem": stem, **{key: options.get(key, "") for key in "ABCDE"}},
                "question_type": "single_choice", "figures": [], "regions": []}

    def locate(self, base=None, text="$x-1=3$", before="$x+1=3$", target="stem", **changes):
        return region_reads.recommendation_for(base or self.base(), text, {
            "target": target, "before": before, "confidence": "high", "why": "完整公式唯一对应", **changes})

    def test_complete_formula_is_located_without_changing_the_original_context(self):
        base = self.base()
        original = deepcopy(base)
        result = self.locate(base)
        self.assertEqual((result["status"], result["field"], result["before"], result["after"]),
                         ("recommended", "stem", "$x+1=3$", "$x-1=3$"))
        self.assertEqual(result["field_text"][result["start"]:result["end"]], result["before"])
        self.assertEqual(result["mode"], "replace_fragment")
        self.assertEqual(base, original)

    def test_js_offsets_count_utf16_for_text_before_the_fragment(self):
        result = self.locate(self.base("😀已知 $x+1=3$。"))
        original = result["field_text"].encode("utf-16-le")
        self.assertEqual(original[result["start"] * 2:result["end"] * 2].decode("utf-16-le"), "$x+1=3$")
        self.assertEqual(result["offset_unit"], "utf16")

    def test_repeated_missing_wrong_field_and_uncertain_proposals_need_manual_location(self):
        for result in (
            self.locate(self.base("$x+1=3$ 与 $x+1=3$")),
            self.locate(self.base(A="$x+1=3$")),
            self.locate(before="$not_found$"),
            self.locate(target="B"),
            self.locate(confidence="low"),
            self.locate(kind="figure"),
            self.locate(text="$x-[?]=3$"),
        ):
            self.assertEqual(result["status"], "manual")
            self.assertTrue(result["after"])

    def test_math_markdown_and_latex_arguments_cannot_be_cut_in_half(self):
        for stem, before, replacement in (
            ("求 $x+1$。", "x+1", "$x-1$"),
            ("求 $x+1$。", "$x+1$", "$x-1"),
            (r"求 $\frac{1}{2}$。", r"\frac{1}", r"\frac{2}{3}"),
            (r"求 \frac{1}{2}。", r"\frac{1}", r"\frac{2}{3}"),
            ("参见 [原文](https://example.test)。", "原文", "新文"),
            ("代码 `x+1`。", "x+1", "x-1"),
        ):
            result = self.locate(self.base(stem), before=before, text=replacement)
            self.assertEqual(result["status"], "manual", (stem, before))
        self.assertEqual(self.locate(self.base(r"求 \frac{1}{2}。"), before=r"\frac{1}{2}",
                                     text=r"$\frac{2}{3}$")["status"], "recommended")

    def test_empty_choice_option_can_receive_whole_text_but_existing_or_picture_option_cannot(self):
        result = self.locate(target="A", before="", text="$1$")
        self.assertEqual((result["status"], result["mode"], result["start"], result["end"]),
                         ("recommended", "whole_field", 0, 0))
        self.assertEqual(self.locate(self.base(A="$2$"), target="A", before="")["status"], "manual")
        picture = self.base()
        picture["figures"] = [{"slot": "A"}]
        self.assertEqual(self.locate(picture, target="A", before="")["status"], "manual")
        self.assertEqual(self.locate(self.base(""), before="")["status"], "manual")
        response = self.base()
        response["question_type"] = "free_response"
        self.assertEqual(self.locate(response, target="A", before="")["status"], "manual")

    def test_markdown_tables_images_and_nested_link_destinations_are_not_cut_apart(self):
        for stem, before, text in (
            ("| 值 | 数量 |\n|---|---|\n| 1 | 2 |", "1", "3"),
            ("值 | 数量\n--- | ---\n1 | 2", "1", "3"),
            ("请看 ![图](/figure.png)", "![图](/figure.png)", "文字"),
            ("参见 [原文](https://example.test/(old))", "[原文](https://example.test/(old)", "新文字"),
            ("参见 [原文](https://example.test/(old)", "原文", "新文字"),
        ):
            self.assertEqual(self.locate(self.base(stem), before=before, text=text)["status"], "manual")
        self.assertEqual(self.locate(self.base("集合 $A=\\{x|x>0\\}$。"),
                                     before="$A=\\{x|x>0\\}$", text="$A=\\{x|x>1\\}$")["status"], "recommended")

    def test_long_inputs_do_not_gain_a_location_or_lose_transcribed_text(self):
        text = "长" * (region_reads.MAX_FRAGMENT_CHARS + 1)
        result = self.locate(text=text)
        self.assertEqual((result["status"], result["after"]), ("manual", text))
        result = self.locate(self.base("长" * (region_reads.MAX_CONTEXT_CHARS + 1)))
        self.assertEqual(result["status"], "manual")
        self.assertIn("较长", result["why"])

    def test_invalid_location_format_and_low_confidence_keep_successful_transcription(self):
        for reply in (
            '{"text":"读出来的字", "target":',
            json.dumps({"text": "读出来的字", "target": "stem", "before": "未找到", "confidence": "high"}),
            json.dumps({"text": "读出来的字", "target": "stem", "before": "$x+1=3$", "confidence": "low"}),
        ):
            text, recommendation = region_reads._auto_reply(reply, self.base())
            self.assertEqual(text, "读出来的字")
            self.assertEqual(recommendation["status"], "manual")
        text, recommendation = region_reads._auto_reply("$x+1=3$", self.base())
        self.assertEqual((text, recommendation["status"]), ("$x+1=3$", "recommended"))

    def test_prompt_uses_current_text_only_for_position_and_keeps_long_context_bounded(self):
        self.assertIn("不能据它补写图片内容", region_reads.prompt("auto", self.base()))
        self.assertIn("before 必须包含完整公式", region_reads.prompt("auto", self.base()))
        large = self.base("长" * (region_reads.MAX_CONTEXT_CHARS + 1))
        self.assertLess(len(region_reads.prompt("auto", large)), 1500)


class RegionRecommendationWorkerTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.question = self.card(self.paper, question_type="single_choice", stem="已知 $x+1=3$，求值。",
                                  options={"A": "$1$", "B": "$2$"}, approved=True,
                                  approved_content_hash="keep-the-human-review", approval_source="human")
        self.url = f"/api/questions/{self.question.id}/region-read"
        patch = mock.patch.object(readers, "primary_engine", return_value=readers.Engine("minimax", "fake-model"))
        patch.start()
        self.addCleanup(patch.stop)

    def post(self, **body):
        data = {"page_idx": 0, "bbox": [100, 80, 500, 120], **body}
        return Client().post(self.url, data=json.dumps(data), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def reply(self, **change):
        return json.dumps({"text": "$x-1=3$", "kind": "text", "target": "stem", "before": "$x+1=3$",
                           "confidence": "high", "why": "完整公式对应", **change})

    def test_auto_queue_captures_base_and_worker_only_adds_a_recommendation(self):
        before = self.question.__class__.objects.filter(pk=self.question.pk).values().get()
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["question"]["region_read"]["target"], "auto")
        with mock.patch.object(readers, "chat", return_value=self.reply()) as chat:
            self.assertEqual(region_reads.process_pending(), 1)
        job = RegionRead.objects.get()
        self.assertEqual((job.status, job.text, job.recommendation["status"]), ("done", "$x-1=3$", "recommended"))
        self.assertEqual(self.question.__class__.objects.filter(pk=self.question.pk).values().get(), before)
        args = chat.call_args
        self.assertIn("$x+1=3$", args.args[1])
        self.assertEqual(len(args.args[2]), 1)
        self.assertEqual(args.kwargs["max_tokens"], 1800)
        shown = region_reads.latest_json(self.question)["recommendation"]
        self.assertNotIn("base", shown)
        self.assertEqual((shown["field"], shown["status"]), ("stem", "recommended"))

    def test_saved_edit_while_reading_marks_location_stale_and_keeps_new_text(self):
        self.post(target="auto")

        def edit_during_read(*args, **kwargs):
            self.question.__class__.objects.filter(pk=self.question.pk).update(stem="后来改过的题干")
            return self.reply()

        with mock.patch.object(readers, "chat", side_effect=edit_during_read):
            region_reads.process_pending()
        self.question.refresh_from_db()
        shown = region_reads.latest_json(self.question)
        self.assertEqual((shown["status"], shown["text"], shown["recommendation"]["status"]),
                         ("done", "$x-1=3$", "stale"))
        self.assertIn("已经变化", shown["recommendation"]["why"])
        self.assertEqual(self.question.stem, "后来改过的题干")
        self.assertTrue(self.question.approved)

    def test_unsafe_or_ambiguous_model_position_still_finishes_with_read_text(self):
        self.post(target="auto")
        with mock.patch.object(readers, "chat", return_value=self.reply(before="x+1=3")):
            region_reads.process_pending()
        job = RegionRead.objects.get()
        self.assertEqual((job.status, job.text, job.recommendation["status"]), ("done", "$x-1=3$", "manual"))

    def test_crop_inside_existing_graph_keeps_text_but_does_not_recommend_replacement(self):
        self.question.figures = [{"slot": "stem", "page_idx": 0, "bbox": [100, 80, 500, 120]}]
        self.question.save()
        self.post(target="auto")
        with mock.patch.object(readers, "chat", return_value=self.reply()):
            region_reads.process_pending()
        job = RegionRead.objects.get()
        self.assertEqual((job.status, job.text, job.recommendation["status"]), ("done", "$x-1=3$", "manual"))
        self.assertIn("已有配图", job.recommendation["why"])

    def test_service_failure_and_legacy_manual_target_remain_explicit(self):
        self.post(target="auto")
        with mock.patch.object(readers, "chat", side_effect=readers.ReaderError("服务不可用")):
            region_reads.process_pending()
        job = RegionRead.objects.get()
        self.assertEqual((job.status, job.error), ("failed", "服务不可用"))
        self.assertEqual(region_reads.latest_json(self.question)["recommendation"]["status"], "manual")
        self.post(target="A")
        with mock.patch.object(readers, "chat", return_value="A. $3$"):
            region_reads.process_pending()
        job = RegionRead.objects.get()
        self.assertEqual((job.status, job.target, job.text), ("done", "A", "$3$"))
        self.assertEqual(job.recommendation["status"], "manual")
        self.assertEqual(self.question.options["A"], "$1$")

    def test_deleted_job_and_retry_cannot_receive_an_old_recommendation(self):
        self.post(target="auto")
        job = RegionRead.objects.get()

        def replace_during_read(*args, **kwargs):
            self.post(target="A")
            return self.reply()

        with mock.patch.object(readers, "chat", side_effect=replace_during_read):
            region_reads.process_pending(limit=1)
        self.assertFalse(RegionRead.objects.filter(pk=job.pk).exists())
        queued = RegionRead.objects.get()
        self.assertEqual((queued.status, queued.target, queued.text), ("queued", "A", ""))

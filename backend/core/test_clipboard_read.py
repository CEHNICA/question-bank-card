"""框选识读到剪贴板：这一路读框里看到的一切（印刷和手写都认），题卡一个字也不动。

和「只誊录印刷体」那一路是两条路：
- 印刷题的识读一个字都不能变（印刷题常混着大量手写，读进去题就没法看了）；
- 这一路是给人搬东西用的：框里有什么读什么，读完抄到剪贴板，随手粘到别处。
"""

import json
from unittest import mock

from django.test import Client, SimpleTestCase, TransactionTestCase

from . import readers, region_reads
from .models import RegionRead
from .test_v110_types_origin import TempDataMixin


class ClipboardPromptTests(SimpleTestCase):
    def test_clipboard_is_a_target_of_its_own(self):
        self.assertIn("clipboard", region_reads.TARGETS)
        self.assertEqual(region_reads.TARGET_NAMES["clipboard"], "复制到剪贴板")

    def test_clipboard_prompt_reads_handwriting_as_well_as_printed_text(self):
        prompt = region_reads.prompt("clipboard")
        self.assertIn("手写", prompt)
        self.assertIn("也要誊录", prompt)
        self.assertIn("都誊录出来", prompt)
        self.assertNotIn("学生手写的字、勾、叉、圈画和批改符号一律忽略", prompt)

    def test_the_printed_only_prompts_are_left_alone(self):
        # 这一路不能碰印刷题的识读：印刷题里常混着手写，读进去题目就没法看了。
        for target in ("stem", "A", "B", "C", "D", "E"):
            prompt = region_reads.prompt(target)
            self.assertIn("请只誊录其中印刷的内容", prompt)
            self.assertIn("学生手写的字、勾、叉、圈画和批改符号一律忽略", prompt)
        self.assertIn("忽略学生手写的字", region_reads.prompt("auto", {"fields": {}}))

    def test_an_empty_box_says_something_different_for_the_clipboard(self):
        with self.assertRaises(region_reads.RegionError) as printed:
            region_reads.clean("无", "stem")
        with self.assertRaises(region_reads.RegionError) as clipboard:
            region_reads.clean("无", "clipboard")
        self.assertIn("印刷文字", str(printed.exception))
        self.assertIn("没有读到文字", str(clipboard.exception))
        self.assertIn("换个位置", str(clipboard.exception))


class ClipboardWorkerTests(TempDataMixin, TransactionTestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.question = self.card(
            self.paper,
            question_type="single_choice",
            stem="已知 $x+1=3$，求值。",
            options={"A": "$1$", "B": "$2$"},
            approved=True,
            approved_content_hash="keep-the-human-review",
            approval_source="human",
        )
        self.url = f"/api/questions/{self.question.id}/region-read"
        patch = mock.patch.object(readers, "primary_engine", return_value=readers.Engine("minimax", "fake-model"))
        patch.start()
        self.addCleanup(patch.stop)

    def post(self, **body):
        data = {"page_idx": 0, "bbox": [100, 80, 500, 120], **body}
        return Client().post(self.url, data=json.dumps(data), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_the_clipboard_read_keeps_the_question_untouched_and_only_sends_the_crop(self):
        before = self.question.__class__.objects.filter(pk=self.question.pk).values().get()
        response = self.post(target="clipboard")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["question"]["region_read"]["target"], "clipboard")
        self.assertEqual(response.json()["question"]["region_read"]["target_name"], "复制到剪贴板")
        with mock.patch.object(readers, "chat", return_value="手写：$\\frac{1}{2}$\n印刷：$x+1=3$") as chat:
            self.assertEqual(region_reads.process_pending(), 1)
        job = RegionRead.objects.get()
        self.assertEqual((job.status, job.target), ("done", "clipboard"))
        self.assertIn("手写", job.text)
        self.assertEqual(job.recommendation["status"], "manual")
        # 题卡一个字也没动。
        self.assertEqual(self.question.__class__.objects.filter(pk=self.question.pk).values().get(), before)
        args = chat.call_args
        self.assertIn("手写", args.args[1])
        self.assertNotIn("$x+1=3$", args.args[1])  # 现有题面不跟着发出去
        self.assertEqual(len(args.args[2]), 1)
        self.assertEqual(args.kwargs["max_tokens"], 1200)

    def test_a_box_with_nothing_in_it_says_what_to_do_next(self):
        self.post(target="clipboard")
        with mock.patch.object(readers, "chat", return_value="无"):
            region_reads.process_pending()
        job = RegionRead.objects.get()
        self.assertEqual(job.status, "failed")
        self.assertIn("没有读到文字", job.error)
        self.assertIn("换个位置", job.error)

    def test_an_unknown_target_is_refused_with_a_readable_reason(self):
        response = self.post(target="随便写的")
        self.assertEqual(response.status_code, 400)
        self.assertIn("方式不正确", response.json()["error"])

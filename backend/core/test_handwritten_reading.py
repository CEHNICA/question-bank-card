"""读不出文字时，先说清是哪一种情况，再决定要不要誊选手写。

老师的原话（2026-10-28）：
  「识别不出来题目之后给他一个反馈……这是手写题啊，这是照片题啊……」
  「还不还不会影响印刷题目的识别，因为印刷题目有时候会混杂着大量的手写，
    如果他真的能把手写看出来，那题目基本上就没法看了。」

所以这一版的规矩是：
  * 印刷题照旧只誊印刷体 —— 誊录提示词一个字没改，印刷题的识别不受影响；
  * 只有「这次一个字都没读出来」时，才多问一句「这块范围里有没有印刷内容」；
  * 一个字印刷都没有，才改成誊录整块手写；只要有一个印刷元素，就不碰手写。
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from . import pipeline, readers
from .models import Question

PRINTED_READING = {
    "stem": "已知 $x=2$，求 $x^{2}$ 的值。",
    "options": {},
    "type": "free_response",
    "content_kind": "exercise",
    "figures": {},
    "missing_figure": False,
    "number_seen": 1,
    "figure_descriptions": [],
    "unclear": False,
    "unreadable": False,
}

EMPTY_READING = {**PRINTED_READING, "stem": "", "content_kind": "unknown", "unreadable": True}

HANDWRITTEN_READING = {
    **PRINTED_READING,
    "stem": "如图，矩形 $ABCD$ 中，$AD=2$，求 $DF$ 的长。",
    "figures": {"1": "stem"},
    "handwritten": True,
}

STORE = type("Store", (), {"load": lambda self, page: object()})()
ENGINE = type("Engine", (), {"label": "测试引擎"})()


def snapshot(**changes):
    base = {
        "id": 1,
        "number": 1,
        "group_id": None,
        "regions": [{"page_idx": 0, "bbox": [80, 160, 900, 240]}],
        "candidates": [],
        "question_type": "free_response",
        "stem": "",
        "options": {},
        "edited": False,
        "source_kind": Question.SourceKind.UNKNOWN,
        "source_anchor_seq": None,
        "segmentation_flags": [],
        "body_mode": "text",
        "start_source": "mineru",
        "witness": "",
        "witness_blocks": [],
    }
    base.update(changes)
    return base


class HandwrittenReadingTests(SimpleTestCase):
    """读出白卷之后的那一步。"""

    def run_card(self, reading, *, printed=None, handwritten=None, **changes):
        """跑一次识读，并把「有没有印刷内容」「誊选手写」两次追问的结果喂进去。"""

        def has_printed_text(engine, image_url):
            if isinstance(printed, Exception):
                raise printed
            return printed

        def read_handwritten(engine, image_url, number):
            if isinstance(handwritten, Exception):
                raise handwritten
            if handwritten is None:
                raise readers.ReaderError("誊录结果里没有正文")
            return handwritten

        with (
            patch.object(pipeline.readers, "primary_engine", return_value=object()),
            patch.object(pipeline.readers, "read_question", return_value=reading),
            patch.object(pipeline.readers, "has_printed_text", side_effect=has_printed_text) as printed_mock,
            patch.object(pipeline.readers, "read_handwritten", side_effect=read_handwritten) as written_mock,
            patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])),
            patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:image/jpeg;base64,x"),
        ):
            result = pipeline.read_card(snapshot(**changes), STORE)
        return result, printed_mock, written_mock

    # ---------------------------------------------------------------- 印刷题不受影响

    def test_a_printed_question_that_reads_fine_is_never_asked_about_handwriting(self):
        result, printed_mock, written_mock = self.run_card(PRINTED_READING)

        self.assertEqual(result["stem"], PRINTED_READING["stem"])
        self.assertEqual(result["state"], Question.State.GREEN)
        self.assertEqual(result["flags"], [])
        # 正常题不多花一次调用，也不改一个字。
        printed_mock.assert_not_called()
        written_mock.assert_not_called()

    def test_an_empty_reading_asks_once_about_printed_content(self):
        result, printed_mock, written_mock = self.run_card(EMPTY_READING, printed=True)

        self.assertEqual(printed_mock.call_count, 1)
        self.assertEqual(written_mock.call_count, 0)
        self.assertIn("印刷文字", result["error"])
        self.assertIn("调整范围", result["error"])
        self.assertIn("AI 识读这题", result["error"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    # ---------------------------------------------------------------- 手写题

    def test_an_all_handwriting_range_is_transcribed(self):
        result, printed_mock, written_mock = self.run_card(
            EMPTY_READING, printed=False, handwritten=HANDWRITTEN_READING
        )

        self.assertEqual(printed_mock.call_count, 1)
        self.assertEqual(written_mock.call_count, 1)
        self.assertEqual(result["stem"], HANDWRITTEN_READING["stem"])
        self.assertEqual(result["text_source"], "handwritten")
        self.assertIn(pipeline.HANDWRITTEN_FLAG, result["flags"])
        # 誊出来的是手写，不是读对了：卡要等人核对。
        self.assertEqual(result["state"], Question.State.YELLOW)
        self.assertEqual(result["error"], "")
        # 誊成了就不是「识读认了输」——那句话不挂。
        self.assertNotIn(pipeline.FLAG_UNREADABLE_STEM, result["flags"])

    def test_a_handwriting_range_that_cannot_be_transcribed_says_what_to_do(self):
        result, _printed_mock, written_mock = self.run_card(
            EMPTY_READING, printed=False, handwritten=readers.ReaderError("誊录结果里没有正文")
        )

        self.assertEqual(written_mock.call_count, 1)
        self.assertIn("手写题", result["error"])
        self.assertIn("改字", result["error"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_an_undecidable_range_gets_both_causes_and_no_transcription(self):
        result, printed_mock, written_mock = self.run_card(EMPTY_READING, printed=readers.ReaderError("服务暂时不可用"))

        self.assertEqual(printed_mock.call_count, 1)
        self.assertEqual(written_mock.call_count, 0)
        self.assertIn("改字", result["error"])
        self.assertIn("调整范围", result["error"])

    def test_the_generic_empty_stem_flag_is_not_shown_next_to_the_explanation(self):
        result, _printed_mock, _written_mock = self.run_card(EMPTY_READING, printed=True)

        self.assertNotIn(pipeline.FLAG_EMPTY_STEM, result["flags"])
        self.assertNotIn(pipeline.FLAG_UNREADABLE_STEM, result["flags"])

    def test_a_handwritten_drawing_becomes_the_question_figure(self):
        result, _printed_mock, _written_mock = self.run_card(
            EMPTY_READING,
            printed=False,
            handwritten=HANDWRITTEN_READING,
            candidates=[{"label": "1", "page_idx": 0, "bbox": [100, 300, 800, 900]}],
        )

        self.assertEqual([figure["slot"] for figure in result["figures"]], ["stem"])

    def test_the_quota_signal_is_not_swallowed(self):
        with self.assertRaises(readers.ReaderQuotaExhausted):
            self.run_card(EMPTY_READING, printed=readers.ReaderQuotaExhausted("额度用完了"))


class PrintedTextCheckTests(SimpleTestCase):
    """「这块范围里有没有印刷内容」这句话怎么算。"""

    def answer(self, raw):
        with patch.object(readers, "chat", return_value=raw):
            return readers.has_printed_text(ENGINE, "data:image/jpeg;base64,x")

    def test_printed_content_is_recognised(self):
        self.assertIs(self.answer("有"), True)
        self.assertIs(self.answer("有印刷内容"), True)

    def test_an_all_handwriting_range_is_recognised(self):
        self.assertIs(self.answer("没有"), False)
        self.assertIs(self.answer("没有印刷内容，整块都是手写"), False)
        self.assertIs(self.answer("无"), False)

    def test_an_unreadable_answer_is_not_a_decision(self):
        # 判不出来不能往「手写」那边倒：猜错一次，印刷题就会被说成手写题。
        self.assertIsNone(self.answer(""))
        self.assertIsNone(self.answer("让我再想想"))

    def test_the_prompt_says_one_printed_mark_is_enough(self):
        prompt = readers.PRINTED_TEXT_PROMPT
        self.assertIn("任何一个印刷出来的元素", prompt)
        self.assertIn("才算「没有」", prompt)


class HandwrittenTranscriptionTests(SimpleTestCase):
    """誊选手写用的是同一套解析，格式不对就不留半截。"""

    def answer(self, raw):
        with patch.object(readers, "chat", return_value=raw):
            return readers.read_handwritten(ENGINE, "data:image/jpeg;base64,x", 1)

    def test_a_handwritten_question_is_transcribed(self):
        raw = "【内容类型】手写\n【题号】\n【题型】解答题\n【题干】如图，求 $DF$ 的长。\n【配图】1=题干"
        reading = self.answer(raw)

        self.assertEqual(reading["stem"], "如图，求 $DF$ 的长。")
        self.assertEqual(reading["type"], "free_response")
        self.assertEqual(reading["figures"], {"1": "stem"})
        self.assertFalse(reading["unreadable"])

    def test_a_refusal_is_not_a_transcription(self):
        with self.assertRaises(readers.ReaderError):
            self.answer("【内容类型】不确定\n【题号】\n【题型】\n【题干】（无法识别图片内容）")

    def test_a_transcription_without_a_type_is_not_treated_as_giving_up(self):
        # 誊出来的是手写，不是「认输了」：手写没写题型只是没写题型，
        # 不该按「识读交了白卷」处理（那会让老师以为这道题读不出来）。
        reading = self.answer("【内容类型】手写\n【题号】\n【题干】求 $x$ 的值。")

        self.assertEqual(reading["stem"], "求 $x$ 的值。")
        self.assertFalse(reading["unreadable"])

    def test_an_empty_body_is_not_a_transcription(self):
        with self.assertRaises(readers.ReaderError):
            self.answer("【内容类型】手写\n【题号】\n【题型】解答题\n【题干】\n【配图】无")

    def test_the_prompt_asks_for_handwriting_and_keeps_drawn_figures(self):
        prompt = readers.handwriting_prompt(1)
        self.assertIn("手写", prompt)
        self.assertIn("编号=题干", prompt)
        self.assertIn("[?]", prompt)

"""识读对着正常截图回一句「（无法识别图片内容）」时，卡不能是绿的。

202510 答案版第 18 题（真实卷子）：切题范围罩住了题目加整段【答案】【解析】
（共 1857 字印刷正文，题目本身完整在第一段），识读却回了 10 个字的占位符，
题型与内容类型都报「未知」，卡照样是绿的、零旗标。用户看到的就是一张空的题卡。

这张图本身没有问题：把它原样重发一次，两次都读对了。所以先重发一次（只在这
种情况下重发，正常题不多花调用），两次都认输才挂提示。

判据为什么是「题型和内容类型同时未知」：只看内容类型会误伤 48周第 12、21 题
——那两张题面读得好好的，只是内容类型判成了未知。两个都未知时，全库 241 张卡
只命中真出问题的这一张。

    .\\backend\\.venv\\Scripts\\python.exe backend\\manage.py test core.test_unreadable_reading
"""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from core import pipeline, readers
from core.models import Question

# 真卷上第 18 题的原文返回，一字未改。
REFUSAL = (
    "【内容类型】未知（图片内容超出可见范围）\n"
    "【题号】未知\n"
    "【题型】未知\n"
    "【题干】\n"
    "（无法识别图片内容）\n"
    "【配图】无\n\n"
    "注：图片内容超出当前对话可处理的范围，无法识别具体的题目内容。"
)

GOOD = (
    "【内容类型】练习题\n"
    "【题号】18\n"
    "【题型】解答题\n"
    "【题干】\n"
    "已知二次函数 $y=-x^2+bx+c$ 的图象与 $x$ 轴的一个交点坐标为 $A(-1，0)$。\n"
    "【配图】无\n"
)


class UnreadableReadingTests(SimpleTestCase):
    def test_a_refusal_is_marked(self):
        self.assertTrue(readers.parse_reading(REFUSAL, 18)["unreadable"])

    def test_an_unknown_content_kind_alone_is_not_a_refusal(self):
        # 48周第 12 题：题型读对了，内容类型判成未知，题面完整。
        raw = GOOD.replace("【内容类型】练习题", "【内容类型】未知")
        self.assertFalse(readers.parse_reading(raw, 18)["unreadable"])

    def test_an_unknown_type_alone_is_not_a_refusal(self):
        raw = GOOD.replace("【题型】解答题", "【题型】未知")
        self.assertFalse(readers.parse_reading(raw, 18)["unreadable"])


class RefusalRetryTests(SimpleTestCase):
    def ask(self, *answers):
        engine = SimpleNamespace(label="MiniMax-M3")
        with mock.patch.object(readers, "chat", side_effect=list(answers)) as chat:
            return readers.read_question(engine, "data:url", 18, with_figures=True), chat

    def test_a_refusal_is_asked_again(self):
        reading, chat = self.ask(REFUSAL, GOOD)
        self.assertEqual(chat.call_count, 2)
        self.assertFalse(reading["unreadable"])
        self.assertIn("二次函数", reading["stem"])

    def test_the_retry_is_recorded_so_it_can_be_audited(self):
        reading, _chat = self.ask(REFUSAL, GOOD)
        self.assertTrue(reading["retried_after_refusal"])

    def test_an_ordinary_reading_is_not_marked_as_retried(self):
        reading, chat = self.ask(GOOD)
        self.assertEqual(chat.call_count, 1)
        self.assertNotIn("retried_after_refusal", reading)

    def test_two_refusals_are_kept_and_reported(self):
        reading, chat = self.ask(REFUSAL, REFUSAL)
        self.assertEqual(chat.call_count, 2)
        self.assertTrue(reading["unreadable"])

    def test_an_ordinary_reading_is_never_asked_twice(self):
        _reading, chat = self.ask(GOOD)
        self.assertEqual(chat.call_count, 1)


def snapshot():
    return {
        "id": 1, "number": 18, "group_id": None,
        "regions": [{"page_idx": 8, "bbox": [62.0, 480.0, 937.0, 583.0]},
                    {"page_idx": 9, "bbox": [60.0, 45.0, 937.0, 474.0]}],
        "candidates": [], "question_type": "unknown", "section": "三、解答题",
        "stem": "", "options": {}, "edited": False,
        "source_kind": Question.SourceKind.UNKNOWN, "source_anchor_seq": 18,
        "segmentation_flags": [], "body_mode": "text", "start_source": "mineru",
        "witness_blocks": [],
    }


class UnreadableFlagTests(SimpleTestCase):
    """提示必须落在卡上，不只是留在 readers 里。"""

    def read(self, reading):
        with mock.patch.object(readers, "primary_engine", return_value=object()), \
                mock.patch.object(readers, "read_question", return_value=reading), \
                mock.patch.object(pipeline.imaging, "stack_regions",
                                  return_value=(SimpleNamespace(height=1614, width=1862), [])), \
                mock.patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:url"):
            return pipeline.read_card(snapshot(), SimpleNamespace(load=lambda page: object()))

    def test_a_refusal_leaves_a_flag_and_yellows_the_card(self):
        result = self.read(readers.parse_reading(REFUSAL, 18))
        self.assertIn(pipeline.FLAG_UNREADABLE_STEM, result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_a_normal_reading_leaves_no_flag(self):
        result = self.read(readers.parse_reading(GOOD, 18))
        self.assertNotIn(pipeline.FLAG_UNREADABLE_STEM, result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN)
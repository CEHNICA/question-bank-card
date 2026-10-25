"""重新切题挂出的旗标，是老师唯一能看到的那句话。

三条规则：
1. 必须是待办而不是通知——范围下次重切一致时它会被自动撤掉，所以文案要落在
   「请去核对」上，不能只陈述系统做了什么。
2. 不说内部词。界面上用户看到的说法是「已入库」（app.js 的筛选就叫这个），
   「入库记录」是实现细节，老师不知道那是什么。
3. 卡面上要读得下去，所以有长度上限。

同样的判据套在重新切题会挂出的每一条旗标上。
"""
from django.test import SimpleTestCase

from . import pipeline

RESEGMENT_FLAGS = [
    pipeline.FLAG_RESEGMENT_PRESERVED,
    pipeline.FLAG_RESEGMENT_EXCLUDED,
    pipeline.FLAG_RESEGMENT_RANGE_PROTECTED,
    pipeline.FLAG_MANUAL_FIGURE_OUTSIDE_RANGE,
]


class ResegmentFlagWordingTests(SimpleTestCase):
    def test_every_flag_asks_the_teacher_to_check(self):
        for flag in RESEGMENT_FLAGS:
            with self.subTest(flag=flag):
                self.assertIn("请", flag,
                              f"旗标要告诉老师该做什么，不能只说系统做了什么：{flag}")

    def test_no_flag_uses_internal_wording(self):
        for flag in RESEGMENT_FLAGS:
            with self.subTest(flag=flag):
                self.assertNotIn("入库记录", flag,
                                 f"「入库记录」是内部说法，界面上叫「已入库」：{flag}")

    def test_protected_flag_does_not_describe_its_own_colour(self):
        """卡片自己会变黄，旗标再说一遍「标黄」只是噪音。"""
        self.assertNotIn("标黄", pipeline.FLAG_RESEGMENT_RANGE_PROTECTED)

    def test_flags_stay_short_enough_to_read_on_a_card(self):
        for flag in RESEGMENT_FLAGS:
            with self.subTest(flag=flag):
                self.assertLessEqual(len(flag), 80,
                                     f"卡面上读不完的旗标：{flag}")

    def test_protected_flag_explains_why_nothing_changed(self):
        """老师看到「重切了但这张没动」会疑惑，得说清是系统没敢动。"""
        flag = pipeline.FLAG_RESEGMENT_RANGE_PROTECTED
        self.assertIn("范围", flag)
        self.assertTrue("通过" in flag or "入库" in flag,
                        f"要说清为什么这张卡没被改动：{flag}")
        self.assertIn("保留", flag)

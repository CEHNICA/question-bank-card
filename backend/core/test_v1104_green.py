"""1.10.4：页面上是绿卡、数据库里还记着旧规则的黄卡，批量标记绿卡通过时不能漏掉。

必修一有 26 张这样的卡：以前的配图检查认为“有候选图没分类”（黄卡），现在的
规则已经自动排除了那张候选图（页面显示“已自动排除疑似多余图”、绿卡），可
“批量标记绿卡通过（26）”只看数据库里的颜色，结果一张也没标上。

全部离线：虚构题目，不调用任何服务。
"""

from __future__ import annotations

import json

from django.test import Client, TestCase

from . import test_v110_types_origin as v110
from .figure_policy import FLAG_UNFOUND_FIGURE
from .models import Question

STEM = "写出下列命题的否定，并判断它们的真假：\n(1) 每个正方形都是平行四边形；\n(2) 存在一个四边形，其内角和不等于 $360^\\circ$."
OLD_REVIEW = {
    "status": "conflict", "source": "automatic", "reason": "有候选图尚未被现有识读明确分类，请确认是否属于本题",
    "signals": ["candidate_unclassified"], "cue_matches": [], "excluded_count": 0, "unclassified_count": 1,
}
READING = {"stem": STEM, "type": "free_response", "figures": {}, "missing_figure": False, "figure_descriptions": []}


class StaleYellowGreenTests(v110.TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.stale = self.card_with_old_review(7)

    def card_with_old_review(self, number):
        return self.card(self.paper, number=number, question_type="free_response", stem=STEM,
                         state=Question.State.YELLOW, flags=[FLAG_UNFOUND_FIGURE], figure_review=dict(OLD_REVIEW),
                         figure_candidates=[{"label": "1", "seq": 5, "page_idx": 0, "bbox": [110, 362, 147, 386]}],
                         read_a=dict(READING), read_b=dict(READING))

    def post(self, url, body=None):
        return Client().post(url, data=json.dumps(body or {}), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_shown_green_and_counted_green(self):
        data = Client().get(f"/api/papers/{self.paper.id}").json()
        shown = data["questions"][0]
        self.assertEqual((shown["state"], shown["flags"], shown["figure_blocked"]), ("green", [], False))
        self.assertEqual(data["paper"]["counts"]["green"], 1)
        self.assertEqual(data["paper"]["counts"]["yellow"], 0)
        # The paper list (worked out from the saved rows) counts it the same way.
        listed = next(item for item in Client().get("/api/papers").json()["papers"] if item["id"] == str(self.paper.id))
        self.assertEqual(listed["counts"], data["paper"]["counts"])
        # Looking does not write.
        self.stale.refresh_from_db()
        self.assertEqual(self.stale.state, Question.State.YELLOW)

    def test_batch_approval_takes_it_and_keeps_it_green(self):
        response = self.post(f"/api/papers/{self.paper.id}/approve-green")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["approved"], 1)
        self.assertEqual(response.json()["paper"]["counts"]["approved"], 1)
        self.stale.refresh_from_db()
        self.assertTrue(self.stale.approved)
        self.assertEqual(self.stale.state, Question.State.GREEN)
        self.assertEqual(self.stale.flags, [])
        self.assertNotEqual(self.stale.figure_review.get("status"), "conflict")

    def test_a_card_still_yellow_after_the_review_is_left_alone(self):
        yellow = self.card(self.paper, number=8, question_type="free_response", stem="已知 $a>b$，求证 $a+1>b+1$",
                           state=Question.State.YELLOW, flags=["两次识读不一致，已由第三次识读裁决"])
        response = self.post(f"/api/papers/{self.paper.id}/approve-green")
        self.assertEqual(response.json()["approved"], 1)
        yellow.refresh_from_db()
        self.assertFalse(yellow.approved)
        self.assertEqual(yellow.state, Question.State.YELLOW)

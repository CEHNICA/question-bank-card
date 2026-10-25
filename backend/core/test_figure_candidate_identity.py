"""1.25.97：配图保存不再被"无关候选图格式不正确"卡死。

两件事叠在一起让老师存不下去：

1. **身份算法两边不一样。** 后端用 Python 的 round(v, 1)（银行家舍入），网页用
   Math.round(v * 10) / 10（逢五进位）。同一个框，90.55 在后端是 90.5、在网页是
   90.6 —— 同一张候选图有两个身份。网页把其中一个发过来，后端说不认识，于是**整份
   配图一起被拒**，老师看到的提示是"重试"，而重试永远不会成功。
2. **改了题目范围之后，之前"与本题无关"的判断失效。** 范围一变，服务端重算了本题
   的候选图；原来那张被标成无关的图已经不在范围内了，但"这张不是本题的图"这个
   判断本身没错。以前服务端会因此拒掉整份配图。

现在的做法：身份算法两边统一；原卷里确实存在的那张图（哪怕已经不在本题范围内）
继续算有效；连原卷里都没有的 key 仍然是格式错误。
"""
from __future__ import annotations

import json
import math

from django.test import TestCase

from core.figure_policy import candidate_key, legacy_candidate_key
from core.models import Block, Paper, Question

from .test_v110_types_origin import TempDataMixin


def _browser_number(value: float) -> str:
    """How the browser prints one coordinate: Math.round(v * 10) / 10, then String()."""

    scaled = float(value) * 10
    rounded = math.floor(scaled + 0.5) / 10 if scaled >= 0 else math.ceil(scaled - 0.5) / 10
    return str(int(rounded)) if rounded == int(rounded) else repr(rounded)


class CandidateIdentityTests(TestCase):
    """网页和服务端必须对同一个框算出同一个身份。"""

    def test_the_browser_rule_and_the_server_rule_agree(self):
        # 这些值在旧规则下两边会分叉：后端 90.5 / 网页 90.6。
        for value in (90.55, 7.25, 0.35, 0.25, 57.05, 123.455, 191.55, 2.675):
            box = {"page_idx": 0, "bbox": [value, value + 10, value + 20, value + 30]}
            server = candidate_key(box)
            browser = "0:" + ",".join(_browser_number(v) for v in (value, value + 10, value + 20, value + 30))
            self.assertEqual(server, browser, f"{value} 两边算出的身份必须一样")

    def test_the_old_rule_really_was_different(self):
        """旧规则确实会让两边分叉 —— 这正是要修掉的那个分叉。"""
        for value in (90.55, 7.25, 0.35, 0.25, 57.05):
            box = {"page_idx": 0, "bbox": [value, value + 10, value + 20, value + 30]}
            self.assertNotEqual(legacy_candidate_key(box), candidate_key(box),
                                f"{value} 在旧规则下两边会算出两个身份")


class IgnoreListAcceptanceTests(TempDataMixin, TestCase):
    """老师标过"与本题无关"的图，不该因为改过范围就再也存不下去。"""

    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.paper.status = Paper.Status.READY
        self.paper.save(update_fields=["status"])
        Block.objects.create(paper=self.paper, seq=10, page_idx=0, type="image",
                             text="", bbox=[100, 120, 300, 320])
        Block.objects.create(paper=self.paper, seq=20, page_idx=0, type="image",
                             text="", bbox=[400, 420, 700, 720])
        self.inside = {"label": "1", "page_idx": 0, "bbox": [100, 120, 300, 320]}
        self.outside = {"label": "2", "page_idx": 0, "bbox": [400, 420, 700, 720]}
        self.inside_key = candidate_key(self.inside)
        self.outside_key = candidate_key(self.outside)
        self.question = self.card(
            self.paper, number=1, question_type="single_choice", stem="下列说法正确的是。",
            options={"A": "甲"}, figures=[], figure_candidates=[self.inside],
            figure_review={"status": "conflict", "signals": ["candidate_unclassified"],
                           "unclassified_count": 1, "excluded_count": 0},
            state=Question.State.YELLOW,
        )

    def post_figures(self, *, figures, ignored):
        return self.client.post(
            f"/api/questions/{self.question.pk}/figures",
            data=json.dumps({"figures": figures, "ignored_candidates": ignored}),
            content_type="application/json", HTTP_X_QB_REQUEST="1",
        )

    def test_an_image_outside_the_current_range_can_still_be_marked_irrelevant(self):
        """那张图已经不在本题范围里，但"它不是本题的图"这个判断仍然算数。"""
        response = self.post_figures(figures=[], ignored=[self.outside_key])
        self.assertEqual(response.status_code, 200, response.content)

    def test_marking_an_out_of_range_image_irrelevant_keeps_that_decision(self):
        """本题有配图时，老师标过的"无关"要原样记下来 —— 改过范围也不能丢。"""
        figure = {"page_idx": 0, "bbox": list(self.inside["bbox"]), "slot": "stem",
                  "candidate_key": self.inside_key}
        response = self.post_figures(figures=[figure], ignored=[self.outside_key])
        self.assertEqual(response.status_code, 200, response.content)
        review = response.json()["question"]["figure_review"]
        self.assertIn(self.outside_key, review["ignored_candidates"])

    def test_a_key_that_exists_nowhere_is_still_refused(self):
        response = self.post_figures(figures=[], ignored=["0:1,1,2,2"])
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("无关候选图", response.json()["error"])

    def test_a_decision_recorded_by_an_older_build_still_matches(self):
        """旧版本记下的身份（上一套四舍五入）也要认，否则老师的判断会悄悄失效。"""
        odd = {"label": "3", "page_idx": 0, "bbox": [90.55, 120, 300, 320]}
        Block.objects.create(paper=self.paper, seq=30, page_idx=0, type="image",
                             text="", bbox=odd["bbox"])
        self.question.figure_candidates = [self.inside, odd]
        self.question.save(update_fields=["figure_candidates"])
        old_key = legacy_candidate_key(odd)
        self.assertNotEqual(old_key, candidate_key(odd))
        response = self.post_figures(figures=[], ignored=[old_key])
        self.assertEqual(response.status_code, 200, response.content)
        review = response.json()["question"]["figure_review"]
        self.assertIn(candidate_key(odd), review["ignored_candidates"])

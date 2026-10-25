"""1.26.30：题目来源（题源）—— 认得出、也改得动。

老师 2026-10-25 晚上说的原话：
「这几个新高考的题目来源它没有识别出来，所以我在想啊，嗯这个要解决一下，
就是它没识别出这个题目来源。然后就是这个题目来源的话，最好是可以修改，放在更多里面。」

教辅常在题前印一段出处：「（2021新高考Ⅰ，7，5分，中）」「（2021全国甲理，13，5分，易）」。
以前这段只要看见「N分」就整段放过 —— 这类标记一个都认不出来：标记留在题干里，
既没法按出处找题，组卷打印时还会印在学生卷子上。

这一版量的是真跑出来的行为：
  1. 那几种标记认得出来了；原来认得出的、认不出的，都还是原来那样；
  2. 「修改题目来源」把出处写进去，题干开头那串括号一并搬走，题干别的一个字不动；
  3. 改字不再带着题源（那件事只在「更多」里做）—— 题源不会被顺手抹掉。

全部离线：虚构题目，不调用任何服务。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import Client, SimpleTestCase, TestCase, override_settings

from . import library, textnorm
from .models import Paper, Question
from .tests import PAGES, fake_page_pdf

# 老师本机上真有的七种标记（2026-10-25 从题库里查出来的）。
REAL_MARKERS = [
    "（2024全国甲理，6，5分，易）",
    "（2021新高考Ⅰ，7，5分，中）",
    "（2021 全国甲理，13，5分，易）",
    "（2024新课标Ⅰ,13,5分，中）",
    "（2022新高考Ⅰ，15，5分，中）",
    "（2021新高考Ⅱ，16，5分，难）",
    "（2022 全国甲文，20，12分，中）",
]

STEM = "若过点 $(a, b)$ 可以作曲线 $y=e^x$ 的两条切线，则"


class OriginRecognitionTests(SimpleTestCase):
    def test_the_markers_the_teacher_pointed_at_are_recognised(self):
        """这七种以前一个都认不出来 —— 标记就留在题干里。"""
        for marker in REAL_MARKERS:
            stem, origin = textnorm.split_origin(marker + STEM)
            self.assertEqual(origin, marker[1:-1], marker)
            self.assertEqual(stem, STEM, marker)

    def test_a_stage_practice_note_is_a_source(self):
        """老师本机题卡 1084 的真实标记：教辅的「阶段练」也是出处。"""
        stem, origin = textnorm.split_origin("（2024 广东佛山阶段练，1）设 $x$ 的极限")
        self.assertEqual(origin, "2024 广东佛山阶段练，1")
        self.assertEqual(stem, "设 $x$ 的极限")

    def test_a_source_note_without_a_score_still_works(self):
        """别把原来就认得出的弄丢了。"""
        cases = {
            "[2026山东枣庄滕州二中月考]已知": ("已知", "2026山东枣庄滕州二中月考"),
            "（2025·北京海淀·期中）已知": ("已知", "2025·北京海淀·期中"),
            "【2024新课标Ⅰ卷】设函数": ("设函数", "2024新课标Ⅰ卷"),
            "（2023·北京卷）已知": ("已知", "2023·北京卷"),
            "(2021浙江卷)已知": ("已知", "2021浙江卷"),
        }
        for text, expected in cases.items():
            self.assertEqual(textnorm.split_origin(text), expected, text)

    def test_a_score_is_not_a_source(self):
        """「本小题满分12分」是卷面的套话，不是出处。"""
        for text in ("（本小题满分12分）已知", "（12分）已知", "(2023年5分)已知", "（多选）下列"):
            self.assertEqual(textnorm.split_origin(text), (text, ""), text)

    def test_a_year_alone_is_not_a_source(self):
        """只有年份、说不出是哪张卷子的，是题干的一部分。"""
        for text in ("（2022年北京冬奥会期间）某商店", "(2020年第七次全国人口普查数据)下表是"):
            self.assertEqual(textnorm.split_origin(text), (text, ""), text)

    def test_the_national_paper_names_need_a_year(self):
        """光一个「全国甲」不算出处 —— 加上年份才算。"""
        self.assertEqual(textnorm.split_origin("（全国甲）已知"), ("（全国甲）已知", ""))
        self.assertEqual(textnorm.split_origin("（2021全国甲）已知"), ("已知", "2021全国甲"))


class TempDataMixin:
    def use_temp_data(self):
        isolated_root = os.environ.get("QB_DATA_ROOT", "").strip()
        if isolated_root:
            Path(isolated_root).mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(dir=isolated_root or None))
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)
        feature_file = mock.patch.dict(os.environ, {"QB_FEATURES_FILE": str(self.temp / "features.json")})
        feature_file.start()
        self.addCleanup(feature_file.stop)

    def make_paper(self, **extra):
        paper = Paper.objects.create(
            filename="IMG20261025105610 等 3 张照片",
            kind="pdf",
            sha256="a" * 64,
            status=Paper.Status.READY,
            pages=PAGES[:1],
            **extra,
        )
        folder = self.temp / str(paper.id)
        folder.mkdir(parents=True, exist_ok=True)
        fake_page_pdf(folder / "source.pdf")
        paper.source_path = str(folder / "source.pdf")
        paper.save()
        return paper

    def card(self, paper, stem=STEM, **extra):
        values = {
            "question_type": "free_response",
            "stem": stem,
            "options": {},
            "regions": [{"page_idx": 0, "bbox": [50, 100, 480, 300]}],
            "state": Question.State.GREEN,
            "text_source": "agree",
            "figure_review": {"status": "confirmed_no_figure", "source": "human", "reason": "原卷无图"},
        }
        values.update(extra)
        return Question.objects.create(paper=paper, number=2, **values)


class OriginEditorTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.client = Client()
        self.paper = self.make_paper()

    def post(self, path, body=None):
        return self.client.post(
            path, data=json.dumps(body or {}), content_type="application/json", HTTP_X_QB_REQUEST="1"
        )

    def test_setting_the_origin_moves_the_printed_note_out_of_the_stem(self):
        """老师补上出处，题干开头那串括号就搬走了 —— 题干别的一个字不动。"""
        stem = "（2021新高考Ⅰ，7，5分，中）" + STEM
        question = self.card(self.paper, stem=stem)
        response = self.post(f"/api/questions/{question.id}/origin", {"origin": "2021新高考Ⅰ，7，5分，中"})
        self.assertEqual(response.status_code, 200, response.content)
        card = response.json()["question"]
        self.assertEqual(card["origin"], "2021新高考Ⅰ，7，5分，中")
        self.assertEqual(card["stem"], STEM)
        question.refresh_from_db()
        self.assertEqual(question.origin, "2021新高考Ⅰ，7，5分，中")
        self.assertEqual(question.stem, STEM)

    def test_the_brackets_the_teacher_pasted_are_cleaned_up(self):
        """老师可能直接把整串括号粘进来 —— 存进去的应该是干净的出处。"""
        question = self.card(self.paper)
        response = self.post(f"/api/questions/{question.id}/origin", {"origin": "【2021新高考Ⅰ，7，5分，中】"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["question"]["origin"], "2021新高考Ⅰ，7，5分，中")

    def test_an_empty_origin_leaves_the_stem_alone(self):
        """留空 = 这道题没有来源。题干一个字都不动，他自己没说要改题干。"""
        stem = "（2021新高考Ⅰ，7，5分，中）" + STEM
        question = self.card(self.paper, stem=stem, origin="2021新高考Ⅰ，7，5分，中")
        response = self.post(f"/api/questions/{question.id}/origin", {"origin": ""})
        self.assertEqual(response.status_code, 200, response.content)
        card = response.json()["question"]
        self.assertEqual(card["origin"], "")
        self.assertEqual(card["stem"], stem)

    def test_a_bad_origin_is_refused(self):
        question = self.card(self.paper)
        for value in (None, 12, ["2021"], "x" * 500):
            response = self.post(f"/api/questions/{question.id}/origin", {"origin": value})
            self.assertEqual(response.status_code, 400, value)
        response = self.post(f"/api/questions/{question.id}/origin", {})
        self.assertEqual(response.status_code, 400)

    def test_changing_the_origin_asks_for_a_fresh_approval(self):
        """题源进了审批的校验值（组卷时可能印在卷面上）—— 改了就要重新核对。"""
        question = self.card(self.paper, origin="2021新高考Ⅰ")
        question.approved = True
        question.approved_content_hash = library.approval_hash(question)
        question.save()
        self.assertTrue(library.approval_is_current(question))
        response = self.post(f"/api/questions/{question.id}/origin", {"origin": "2022全国甲理"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["question"]["approved"])

    def test_saving_the_text_keeps_the_origin(self):
        """改字不再带着题源了 —— 那件事只在「更多」里做。题源不能被顺手抹掉。

        1.26.27 踩过同款坑：只改了一个入口，另一个入口在保存时把已有的关系抹掉，
        界面上一点提示都没有。这里守着这条线。
        """
        question = self.card(self.paper, origin="2021新高考Ⅰ，7，5分，中")
        response = self.post(
            f"/api/questions/{question.id}/text",
            {"stem": STEM + "（改过的）", "options": {}, "question_type": "free_response"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        card = response.json()["question"]
        self.assertEqual(card["origin"], "2021新高考Ⅰ，7，5分，中")
        question.refresh_from_db()
        self.assertEqual(question.origin, "2021新高考Ⅰ，7，5分，中")

    def test_a_note_in_front_of_the_stem_is_kept_when_the_card_already_has_one(self):
        """题干里还有另一段出处时，不擅自删他写的那一段。"""
        question = self.card(self.paper, stem="（2022·海淀期末）" + STEM, origin="2021新高考Ⅰ，7，5分，中")
        response = self.post(f"/api/questions/{question.id}/origin", {"origin": "2021新高考Ⅰ，7，5分，中"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["question"]["stem"], "（2022·海淀期末）" + STEM)

    def test_the_teacher_may_write_the_source_in_his_own_words(self):
        """老师记的是「2021新高考Ⅰ」，卷上印的是「2021新高考Ⅰ，7，5分，中」——
        说的是同一件事，那段括号照样搬走。"""
        stem = "（2021新高考Ⅰ，7，5分，中）" + STEM
        question = self.card(self.paper, stem=stem)
        response = self.post(f"/api/questions/{question.id}/origin", {"origin": "2021新高考Ⅰ"})
        self.assertEqual(response.status_code, 200, response.content)
        card = response.json()["question"]
        self.assertEqual(card["origin"], "2021新高考Ⅰ")
        self.assertEqual(card["stem"], STEM)

    def test_a_source_behind_a_column_label_is_still_movable(self):
        """老师本机题卡 1100：出处前面压着一道栏目名。

        栏目名是教辅的栏，不是出处 —— 留在题干里；出处那一段摘走。
        """
        stem = "（创新考法）（2024江苏南通一中月考，6）牛顿迭代法是求方程近似解的一种方法。"
        question = self.card(self.paper, stem=stem)
        response = self.post(f"/api/questions/{question.id}/origin", {"origin": "2024江苏南通一中月考，6"})
        self.assertEqual(response.status_code, 200, response.content)
        card = response.json()["question"]
        self.assertEqual(card["origin"], "2024江苏南通一中月考，6")
        self.assertEqual(card["stem"], "（创新考法）牛顿迭代法是求方程近似解的一种方法。")

    def test_a_no_op_save_does_not_touch_the_stem(self):
        """老师什么都没改就点保存 —— 题干一个字都不能动。"""
        stem = "（2022·海淀期末）" + STEM
        question = self.card(self.paper, stem=stem, origin="2021新高考Ⅰ")
        response = self.post(f"/api/questions/{question.id}/origin", {"origin": "2021新高考Ⅰ"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["question"]["stem"], stem)

"""判定结果落盘复用，和「记住的判定要跟着题目变」这条规矩接在一起。

``views.card_verdicts`` 早就有「记住的判定」：同一道题的判定算过一次就记住，
题目改了指纹就变、判定跟着重算。1.26.50 把这份「记住」从内存搬到磁盘，
所以这里盯住两件事：

1. 跨进程复用时，指纹一样才能用旧判定；指纹不一样必须重算 ——
   不然老师改了题，界面上还显示旧结论。
2. 落盘的那份和内存里算出来的那份，结论必须一模一样。

判断「有没有真的复用」不看结果对不对（重算一遍也对），
而是数一数重算了几次 —— 复用时不该再算一次。

这些都用真库跑，但只造几张虚拟题，不碰老师的数据。
"""

from __future__ import annotations

import tempfile
from collections import OrderedDict
from pathlib import Path
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from core import library, verdict_cache, views
from core.models import Question

from . import test_v110_types_origin as v110


class VerdictCacheReuseTests(v110.TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.folder = Path(tempfile.mkdtemp())
        self.cache_file = self.folder / "verdict-cache.json"
        # 记忆判定是全局的：别的测试往里写过东西。
        # 测之前清空，测完原样还回去 —— 不然比的就不是这一份卷子了。
        original = views._VERDICTS
        views._VERDICTS = OrderedDict()
        self.addCleanup(setattr, views, "_VERDICTS", original)

    def make_cache(self) -> verdict_cache.DiskVerdictCache:
        return verdict_cache.DiskVerdictCache(path=self.cache_file)

    def remember(self) -> dict[int, tuple]:
        """把此刻记住的判定抄一份（含指纹）—— 只限这一份卷子自己的题。"""
        mine = set(self.paper.questions.values_list("pk", flat=True))
        return {pk: tuple(value) for pk, value in views._VERDICTS.items() if pk in mine}

    def count_recalculations(self):
        """数一数这一遍重算了几次。"""
        counter = {"n": 0}
        original = views._verdict

        def counted(*args, **kwargs):
            counter["n"] += 1
            return original(*args, **kwargs)

        return counter, mock.patch.object(views, "_verdict", counted)

    def test_a_verdict_survives_a_restart_and_still_follows_the_card(self):
        card = self.card(
            self.paper, number=1, stem="第 1 题：已知 $x>1$，求 $x^2$ 的范围", question_type="free_response"
        )
        library.approve(card, now=timezone.now())
        card.save()

        # 第一次：算一遍，记住，存到磁盘。
        first = views.card_verdicts(self.paper)
        self.assertTrue(first, "第一遍要算得出判定")
        remembered = self.remember()
        self.assertTrue(remembered, "算过的判定要记住")

        cache = self.make_cache()
        for pk, value in remembered.items():
            cache[pk] = value
        cache.flush()

        # 相当于重开软件：从磁盘读回来，填回记忆里。
        restored = self.make_cache()
        self.assertEqual(len(restored), len(remembered), "存过的判定要能读回来")
        views._VERDICTS = restored

        # 指纹对得上 → 直接用旧判定，不重算。
        counter, patcher = self.count_recalculations()
        with patcher:
            again = views.card_verdicts(self.paper)
        self.assertEqual(counter["n"], 0, "指纹一样就不该再算一遍")
        self.assertEqual(
            {pk: tuple(rest) for pk, *rest in again},
            {pk: tuple(rest[1:]) for pk, rest in remembered.items()},
            "复用出来的结论要和原来一样",
        )

        # 指纹变了 → 必须重算，不能拿旧结论糊弄老师。
        Question.objects.filter(pk=card.pk).update(stem="第 1 题：改过的题干")
        counter, patcher = self.count_recalculations()
        with patcher:
            changed = views.card_verdicts(self.paper)
        self.assertEqual(counter["n"], 1, "题干改了，那道题必须重算")
        changed_map = {pk: rest for pk, *rest in changed}
        self.assertIn(card.pk, changed_map, "改过的题也要有判定")
        # 这道题本来是「已通过」的；题干一改，通过状态就该掉下来 —
        # 如果还在显示「通过」，那就是拿旧结论糊弄老师。
        self.assertEqual(
            tuple({pk: rest[1:] for pk, rest in remembered.items()}[card.pk])[1], True, "改题干之前，这道题是已通过的"
        )
        self.assertNotEqual(
            tuple(changed_map[card.pk]),
            tuple({pk: rest[1:] for pk, rest in remembered.items()}[card.pk]),
            "题干改了，结论不能还是旧的",
        )

    def test_the_disk_copy_says_the_same_as_the_fresh_calculation(self):
        self.card(self.paper, number=1, stem="第 1 题")
        self.card(self.paper, number=2, stem="第 2 题")

        fresh = {pk: rest for pk, *rest in views.card_verdicts(self.paper)}
        remembered = self.remember()

        cache = self.make_cache()
        for pk, value in remembered.items():
            cache[pk] = value
        cache.flush()

        stored = self.make_cache()
        self.assertEqual(
            {pk: tuple(stored[pk][1:]) for pk in fresh},
            {pk: tuple(value) for pk, value in fresh.items()},
            "存到磁盘的结论要和刚算出来的一样",
        )

    def test_an_approved_card_still_counts_as_approved_after_a_restart(self):
        card = self.card(self.paper, number=1, stem="第 1 题", question_type="free_response")
        library.approve(card, now=timezone.now())
        card.save()

        views.card_verdicts(self.paper)
        remembered = self.remember()
        cache = self.make_cache()
        for pk, value in remembered.items():
            cache[pk] = value
        cache.flush()

        restored = self.make_cache()
        self.assertEqual(restored[card.pk][2], True, "已经通过的题，读回缓存后还得是通过")

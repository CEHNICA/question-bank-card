"""1.26.33：识读不能卡在「正在识读」出不来。

老师 2026-10-28 的反馈：点「AI 识读这题」，界面一直转，写着「AI 正在读取左侧原图……」。

查下来（老师本机的日志和库）：
  * 15:48:38 提交识读 → 15:48:45 后台工作者写结果时撞上 SQLite「database is locked」，
    那次写没进去 → **题卡停在「正在识读」，而队列里已经没有任何东西会再来接手**；
  * 于是题卡永远转圈，而且没有一句话能说清发生了什么。

这一版量的是真跑出来的行为：
  1. 写识读结果撞上库锁 → 重来一次，**结果不丢**（识读是一次模型调用的钱）；
  2. worker 启动时（或一批识读失败时）→ 把卡住的题卡放出来，写清原因，
     老师点一下就能重试，不再永远转圈；
  3. 排队的识读请求（还没开始读的那些）不受影响。
"""

from __future__ import annotations

import sqlite3
from unittest import mock

from django.db.utils import OperationalError
from django.test import TestCase

from . import pipeline, test_manual_intake_review as manual_review
from .models import Question


def locked_error() -> OperationalError:
    """一条跟后台真正收到的那条一样的锁错。

    代码靠驱动的错误码认锁冲突（Django 把 sqlite3 的错包了一层，
    错误码在 __cause__ 里）。伪造的时候得把码带上，否则它认不出来 ——
    这正是「只看错误文字会认错」的地方。
    """
    cause = sqlite3.OperationalError("database is locked")
    cause.sqlite_errorcode = sqlite3.SQLITE_BUSY
    error = OperationalError("database is locked")
    error.__cause__ = cause
    return error


class _ReadingFixture(TestCase):
    """借用别人的建卷方法，但不要把人家的测试也重跑一遍。"""

    setUp = manual_review.ManualIntakeReviewTests.setUp
    paper = manual_review.ManualIntakeReviewTests.paper


class ReadingWriteRetriesTests(_ReadingFixture):
    def test_a_lock_that_clears_on_the_second_try_keeps_the_result(self):
        """第一次撞锁、第二次成功 → 结果留下，不报错。"""
        calls = []

        def write():
            calls.append(1)
            if len(calls) == 1:
                raise locked_error()

        with mock.patch.object(pipeline, "time") as clock:
            clock.sleep = lambda seconds: None
            pipeline._write_reading_result(write)
        self.assertEqual(len(calls), 2, "第二次就该写进去")

    def test_a_lock_that_never_clears_is_given_up_on_after_three_tries(self):
        """一直撞锁 → 重来三次，然后才真的放弃（不能无限重来）。"""
        calls = []

        def write():
            calls.append(1)
            raise locked_error()

        with mock.patch.object(pipeline, "time") as clock:
            clock.sleep = lambda seconds: None
            with self.assertRaises(OperationalError):
                pipeline._write_reading_result(write)
        self.assertEqual(len(calls), 3, "一次锁 + 两次重试；第三次还不行才真的放弃")

    def test_an_error_that_is_not_a_lock_is_not_retried(self):
        """不是锁的问题就别重来了 —— 直接报出去。"""
        calls = []

        def write():
            calls.append(1)
            raise OperationalError("database schema is corrupt")

        with mock.patch.object(pipeline, "time") as clock:
            clock.sleep = lambda seconds: None
            with self.assertRaisesRegex(OperationalError, "schema is corrupt"):
                pipeline._write_reading_result(write)
        self.assertEqual(len(calls), 1, "不是锁冲突就不该重试")


class ReadingRecoveryTests(_ReadingFixture):
    def stuck_question(self, *, reread_requested=False, pending=True):
        paper = self.paper()
        return Question.objects.create(
            paper=paper,
            number=1,
            body_mode="source_image",
            processing_mode="manual",
            question_type="free_response",
            state=Question.State.READING,
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 420]}],
            ocr_pending=pending,
            reread_requested=reread_requested,
        )

    def test_a_stuck_question_is_released_with_a_readable_reason(self):
        """卡在「正在识读」的题卡要自己出来，并说清发生了什么。"""
        question = self.stuck_question()
        released = pipeline.recover_interrupted_readings()
        self.assertEqual(released, 1)
        question.refresh_from_db()
        self.assertFalse(question.ocr_pending, "不能还停在「正在识读」")
        self.assertFalse(question.reread_requested)
        self.assertIn("识读", str(question.ocr_suggestion.get("error") or ""))
        self.assertTrue(str(question.error or ""), "题卡上要有一句说清原因的话")

    def test_a_queued_request_is_not_released(self):
        """还没开始读的排队请求不能被当成卡住的 —— 它还得照常读。"""
        question = self.stuck_question(reread_requested=True)
        released = pipeline.recover_interrupted_readings()
        self.assertEqual(released, 0)
        question.refresh_from_db()
        self.assertTrue(question.ocr_pending, "排队的识读不能被清掉")
        self.assertTrue(question.reread_requested)

    def test_a_batch_that_dies_releases_its_questions(self):
        """一批识读中途失败（写不进去）时，那批题要放出来，不能永远转圈。"""
        question = self.stuck_question(reread_requested=True)
        with mock.patch.object(pipeline, "read_questions", side_effect=RuntimeError("写不进去")):
            with self.assertRaises(RuntimeError):
                pipeline.process_rereads()
        question.refresh_from_db()
        self.assertFalse(question.ocr_pending, "失败的题卡要放出来")
        self.assertTrue(str(question.error or ""), "要有一句说清原因的话")


class WorkerStartupRecoveryTests(_ReadingFixture):
    def test_the_worker_releases_stuck_questions_when_it_starts(self):
        """worker 启动时会把上次没干完的题卡放出来。"""
        from core.management.commands import run_worker

        question = Question.objects.create(
            paper=self.paper(),
            number=1,
            body_mode="source_image",
            processing_mode="manual",
            question_type="free_response",
            state=Question.State.READING,
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 420]}],
            ocr_pending=True,
            reread_requested=False,
        )
        run_worker.recover_interrupted_readings()
        question.refresh_from_db()
        self.assertFalse(question.ocr_pending)
        self.assertTrue(str(question.error or ""))

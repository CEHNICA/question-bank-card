"""生成要花钱，所以开关旁边得摆着「还差几道」。

「还差几道」这个数字只有一处算：``library.generation_backlog``，判据和真正排队的
``library_jobs.queue_on_intake`` 同一个。这里钉住的是两边不会各数一遍——
数字对不上，用户就会以为白花了钱或者白等了。
"""

from __future__ import annotations

from unittest import mock

from django.test import TestCase

from core import library, library_jobs
from core.models import PublishedQuestion

from .test_v110_types_origin import TempDataMixin


class GenerationBacklogTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()

    def publication(self, number, *, tags=(), answer="", ai_answer=None, status="published"):
        content = {"question_type": "free_response", "stem": f"第 {number} 题", "options": {},
                   "figures": [], "sources": [], "answer": answer, "analysis": "", "source_filename": "卷.pdf"}
        extras = {"tags": list(tags)}
        if ai_answer is not None:
            extras["ai_answer"] = {"answer": ai_answer, "fingerprint": "f"}
        return PublishedQuestion.objects.create(
            source_filename="卷.pdf", number=number, question_type="free_response", version=1,
            content=content, content_hash=library.content_hash(content), extras=extras,
            tags_text=library.tags_text(tags), status=status)

    def test_counts_only_what_is_still_missing(self):
        self.publication(1, tags=["集合的概念"], answer="原卷有")
        self.publication(2, tags=["集合的概念", "随机抽样"], answer="原卷有")
        self.publication(3, tags=["随机抽样"], answer="", ai_answer="AI 初稿")
        self.publication(4)
        # 只有第 4 题两样都缺：AI 初稿也算「有答案」，用户不用再花一次钱。
        self.assertEqual(library.generation_backlog(), {"tags": 1, "answer": 1, "total": 4})

    def test_withdrawn_and_superseded_versions_are_not_promised_any_work(self):
        self.publication(1)
        self.publication(2, status=PublishedQuestion.Status.WITHDRAWN)
        self.publication(3, status=PublishedQuestion.Status.SUPERSEDED)
        self.assertEqual(library.generation_backlog(), {"tags": 1, "answer": 1, "total": 1})

    def test_the_backlog_and_the_queue_agree_on_the_same_questions(self):
        """同一批题：面板上写着还差几道，实际排进去的就该是几道。"""
        ready = self.publication(1, tags=["集合的概念"], answer="原卷有")
        half = self.publication(2, tags=["集合的概念"])
        bare = self.publication(3)
        backlog = library.generation_backlog()
        self.assertEqual((backlog["tags"], backlog["answer"]), (1, 2))

        status = {"mode": "assistant", "on_intake": {"tags": True, "answer": True}}
        with mock.patch.object(library_jobs.library_ai_settings, "public_status", return_value=status), \
             mock.patch.object(library_jobs.features, "enabled", return_value=True), \
             mock.patch.object(library_jobs, "enqueue") as enqueue:
            enqueue.side_effect = lambda publication, kind: library_jobs.LibraryJob(
                publication=publication, kind=kind)
            for publication in (ready, half, bare):
                library_jobs.queue_on_intake(publication)
            queued_tags = sum(1 for call in enqueue.call_args_list if call.args[1] == "tags")
            queued_answers = sum(1 for call in enqueue.call_args_list if call.args[1] == "answer")
        self.assertEqual((queued_tags, queued_answers), (backlog["tags"], backlog["answer"]))

"""A bulk button must print the number pressing it will really queue.

The list page used one count for two different questions: "this question has no
knowledge tag yet" and "pressing this will create a job for it". Questions whose
suggestion is already waiting or running belong to the first set and not the
second. Printing the first number on the button promises work that never happens,
which reads exactly like a dead button — the same complaint as no feedback at all.
"""

import json
import os
from unittest import mock

from django.test import TransactionTestCase
from django.utils import timezone

from . import features, library, library_ai_settings as service, library_browse, library_job_control, library_jobs
from .models import LibraryJob, LibraryTag, LibraryTagAssignment, LibraryTagSuggestion, PublishedQuestion
from .test_library_ai_settings import answer_response, protected_test_bytes
from .test_v110_types_origin import TempDataMixin


class BatchButtonTruthTests(TempDataMixin, TransactionTestCase):
    def setUp(self):
        self.use_temp_data()
        environment = mock.patch.dict(os.environ, {
            "QB_LIBRARY_AI_SETTINGS_FILE": str(self.temp / "ai-settings.json"),
            "QB_LIBRARY_AI_CREDENTIAL_FILE": str(self.temp / "ai-key.dat"),
            "QB_FEATURES_FILE": str(self.temp / "features.json"),
            "QB_CREDENTIAL_FILE": str(self.temp / "ocr-key.dat"),
        })
        environment.start(); self.addCleanup(environment.stop)
        transform = mock.patch.object(service.credential_settings.store, "_transform", side_effect=protected_test_bytes)
        transform.start(); self.addCleanup(transform.stop)
        transport = mock.patch.object(service.requests, "post", side_effect=AssertionError("unexpected cloud request"))
        self.transport = transport.start(); self.addCleanup(transport.stop)
        features.save({"ai_answer": True, "knowledge_tags": True})
        self.ready_api()

    def ready_api(self):
        service.save({"mode": "api", "endpoint_id": "ep-offline-batch", "supports_images": True, "thinking": True,
                      "key": {"action": "replace", "value": "synthetic-batch-api-key"}})
        service.save({"mode": "api"})
        self.transport.side_effect = None
        self.transport.return_value = answer_response()
        self.assertTrue(service.test_connection({"confirm": True})["api_ready"])
        self.transport.reset_mock()
        self.transport.side_effect = AssertionError("unexpected cloud request")

    def publication(self, number, *, answer="", extras=None):
        content = {"question_type": "free_response", "stem": f"离线第 {number} 题，求 $1+{number}$。", "options": {},
                   "figures": [], "sources": [], "answer": answer, "analysis": "", "source_filename": "isolated.pdf"}
        return PublishedQuestion.objects.create(source_filename="isolated.pdf", number=number,
            question_type="free_response", version=1, content=content,
            content_hash=library.content_hash(content), extras=extras or {})

    def waiting_suggestion(self, publication, status=LibraryTagSuggestion.Status.PENDING):
        from .models import LibraryTagCatalogue
        return LibraryTagSuggestion.objects.create(publication=publication,
            question_fingerprint=library.generation_fingerprint(publication.content, publication.pk),
            taxonomy_revision=LibraryTagCatalogue.objects.get(pk=1).revision,
            assignment_revision=publication.tag_revision, method_source={}, suggestions=[],
            status=status, message="排队中。")

    def job(self, publication, kind, *, executor="api", status="queued"):
        return LibraryJob.objects.create(publication=publication, kind=kind, executor=executor, status=status,
            solution_scope=False, tag_scope=kind == "tags",
            fingerprint=library.generation_fingerprint(publication.content, publication.pk))

    def post(self, payload):
        return self.client.post("/api/library/jobs", json.dumps(payload), content_type="application/json",
                                HTTP_X_QB_REQUEST="1")

    def test_tag_button_counts_the_questions_pressing_it_really_queues(self):
        first, second, third = self.publication(1), self.publication(2), self.publication(3)
        work = library_browse.tag_work_counts()
        self.assertEqual(work["untagged_knowledge"], 3)
        self.assertEqual(work["untagged_ready"], 3)

        # 已经在等、在跑、已经生成好等核对的，都还是「没打标签」，
        # 可再点一次并不会为它们建任务 —— 按钮必须改报后一个数。
        for publication, status in [(first, LibraryTagSuggestion.Status.QUEUED),
                                    (second, LibraryTagSuggestion.Status.RUNNING),
                                    (third, LibraryTagSuggestion.Status.PENDING)]:
            self.waiting_suggestion(publication, status)
        work = library_browse.tag_work_counts()
        self.assertEqual(work["untagged_knowledge"], 3, "the untagged filter still counts all three")
        self.assertEqual(work["untagged_ready"], 0, "but none of them can be queued again")

        fresh = self.publication(4)
        self.assertEqual(library_browse.tag_work_counts()["untagged_ready"], 1)
        response = self.post({"kind": "tags", "missing": True, "api_only": True})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["queued"], 1, "the button's own number is what gets queued")
        queued = set(LibraryJob.objects.filter(kind="tags").values_list("publication_id", flat=True))
        self.assertEqual(queued, {fresh.pk}, "questions already waiting or running get no second job")

    def test_answer_button_counts_the_questions_pressing_it_really_queues(self):
        self.publication(1)
        self.publication(2)
        self.publication(3, answer="原卷答案")
        self.publication(4, extras={"ai_answer": {"answer": "已有 AI 参考"}})
        self.publication(5, answer="   ")
        self.assertEqual(library_browse.tag_work_counts()["missing_answer"], 3)

        response = self.post({"kind": "answer", "missing": True, "api_only": True})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["queued"], 3, "the answer button's number is what gets queued")

    def test_answer_counts_all_use_the_whole_library_so_they_never_subtract_into_a_negative(self):
        # 界面上那句「题库里没有入库答案的是 N 题，其中 M 题已有 AI 初稿」原来拿的是
        # 当前筛选下的 N，去减全库的 M —— 两个口径混着减，负数就是这么来的。
        # 三个数现在都按全库算，且互相之间只差「有没有 AI 初稿」。
        self.publication(1)
        self.publication(2)
        self.publication(3, answer="原卷答案")
        self.publication(4, extras={"ai_answer": {"answer": "已有 AI 参考"}})
        work = library_browse.tag_work_counts()
        self.assertEqual(work["missing_answer"], 2, "没有答案、也没有 AI 初稿")
        self.assertEqual(work["ai_draft_answers"], 1, "没有答案、但已经有 AI 初稿")
        self.assertEqual(work["missing_answer_total"], 3, "没入库答案的全部")
        self.assertEqual(work["missing_answer_total"] - work["missing_answer"],
                         work["ai_draft_answers"], "两个数之差必须正好等于第三个数")
        self.assertGreaterEqual(work["missing_answer_total"], work["missing_answer"])

    def test_the_library_tells_the_page_whether_a_key_was_ever_filled_in(self):
        # 题库页要分清「还没填密钥」「填了但没测过」「测过了」，
        # 只给一个 api_ready，前端只能写一句笼统的话。
        payload = self.client.get("/api/library", HTTP_X_QB_REQUEST="1").json()
        for key in ("api_ready", "key_configured", "configured", "status"):
            self.assertIn(key, payload["ai"])
        self.assertIn(payload["ai"]["status"], ("assistant", "verified", "unverified", "missing"))

    def test_working_counts_report_only_active_api_jobs(self):
        busy_tag = self.publication(1)
        busy_answer = self.publication(2)
        self.publication(3)
        self.job(busy_tag, "tags")
        self.job(busy_answer, "answer")
        self.job(self.publication(4), "tags", status="done")
        self.job(self.publication(5), "answer", executor="assistant")
        work = library_browse.tag_work_counts()
        self.assertEqual(work["queued_tags"], 1)
        self.assertEqual(work["queued_answers"], 1, "assistant work waits for a human, so it is not 'being processed'")

    def test_a_batch_sweeps_stale_jobs_once_instead_of_once_per_question(self):
        for number in range(1, 5):
            self.publication(number)
        self.assertEqual(library_browse.tag_work_counts()["missing_answer"], 4)

        with mock.patch.object(library_job_control, "expire_api_jobs", return_value=0) as sweep, \
             mock.patch.object(library_jobs, "enqueue", wraps=library_jobs.enqueue):
            response = self.post({"kind": "answer", "missing": True, "api_only": True})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["queued"], 4)
        # 整批扫一次就够。原来是每道题扫一次，整库排队时这项单独就吃掉好几秒，
        # 而那段时间界面上什么都不动，看着像卡死。
        self.assertEqual(sweep.call_count, 1, "one sweep for the whole batch, not one per question")

    def test_a_single_question_still_sweeps_before_it_queues(self):
        publication = self.publication(1)
        with mock.patch.object(library_job_control, "expire_api_jobs", return_value=0) as sweep:
            self.assertEqual(library_jobs.enqueue(publication, "answer", api_only=True).executor, "api")
        self.assertEqual(sweep.call_count, 1, "the ordinary single-job path keeps its own sweep")

    def test_a_question_with_a_confirmed_tag_is_neither_missing_nor_queueable(self):
        publication = self.publication(1)
        tag = LibraryTag.objects.create(name="统计用知识点", kind="knowledge", levels=["高中"])
        LibraryTagAssignment.objects.create(publication=publication, tag=tag, source="human", needs_review=False)
        work = library_browse.tag_work_counts()
        self.assertEqual(work["untagged_knowledge"], 0)
        self.assertEqual(work["untagged_ready"], 0)

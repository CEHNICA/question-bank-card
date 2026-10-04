"""1.12.6：题库要能一次撤回一批题。

之前只有单题撤回，清一份 25 题的卷得点 25 下，做不完。批量端点一个事务里
逐条撤回并逐条回报：撤了几条、哪几条没撤、为什么没撤。不静默吞掉，否则
用户以为 25 道全撤了，其实有几道根本没动。
"""

from copy import deepcopy

from django.test import Client, TestCase

from . import library
from .models import Paper, PublishedQuestion, Question


class LibraryWithdrawBatchTests(TestCase):
    def setUp(self):
        self.serial = 0

    def publication(self, *, paper=None, question=None, version=1, status="published"):
        self.serial += 1
        if question:
            paper = question.paper
        paper = paper or Paper.objects.create(filename=f"资料{self.serial}.pdf", kind="pdf", sha256="a" * 64)
        question = question or Question.objects.create(paper=paper, number=self.serial, question_type="single_choice",
                                                       stem=f"虚构题卡 {self.serial}", state="green")
        content = deepcopy(library.final_content(question))
        content.update(document_id=str(paper.id), source_filename=paper.filename)
        return PublishedQuestion.objects.create(
            paper=paper, question=question, source_filename=paper.filename, number=self.serial,
            question_type=question.question_type, version=version, status=status, content=content,
            content_hash=library.content_hash(content), review_source="human")

    def batch(self, ids):
        return Client().post("/api/library/withdraw-batch", {"ids": list(ids)},
                             content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_a_whole_paper_can_be_withdrawn_in_one_call(self):
        paper = Paper.objects.create(filename="月考卷.pdf", kind="pdf", sha256="b" * 64)
        rows = [self.publication(paper=paper) for _ in range(25)]
        response = self.batch([row.id for row in rows])
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["withdrawn_count"], 25)
        self.assertEqual(body["skipped"], [])
        self.assertEqual(PublishedQuestion.objects.filter(status="published").count(), 0)
        # 撤回是可逆的：记录还在，只是状态变了，题卡和原卷都保留。
        self.assertEqual(PublishedQuestion.objects.filter(status="withdrawn").count(), 25)

    def test_each_skipped_row_says_why_instead_of_disappearing(self):
        gone = self.publication()
        already = self.publication(status="withdrawn")
        old = self.publication(status="superseded")
        good = self.publication()
        missing = "00000000-0000-4000-8000-000000000000"
        body = self.batch([good.id, already.id, old.id, missing]).json()
        self.assertEqual(body["withdrawn"], [str(good.id)])
        reasons = {row["id"]: row["reason"] for row in body["skipped"]}
        self.assertEqual(set(reasons), {str(already.id), str(old.id), missing})
        self.assertIn("已经撤回过", reasons[str(already.id)])
        self.assertIn("已被新版替代", reasons[str(old.id)])
        self.assertIn("已经没有这道题", reasons[missing])
        self.assertEqual(PublishedQuestion.objects.get(pk=gone.id).status, "published",
                         "不在这一批里的题不能被动到")

    def test_repeating_an_id_withdraws_once_and_reports_it_once(self):
        row = self.publication()
        body = self.batch([row.id, row.id]).json()
        self.assertEqual(body["withdrawn"], [str(row.id)])
        self.assertEqual(body["skipped"], [])

    def test_empty_and_oversized_and_malformed_requests_are_refused(self):
        self.assertEqual(self.batch([]).status_code, 400)
        self.assertIn("请先勾选", self.batch([]).json()["error"])
        self.assertEqual(Client().post("/api/library/withdraw-batch", {"ids": "not-a-list"},
                                       content_type="application/json", HTTP_X_QB_REQUEST="1").status_code, 400)
        self.assertEqual(Client().get("/api/library/withdraw-batch", HTTP_X_QB_REQUEST="1").status_code, 405)
        # 超过既有批量上限沿用 library_browse 的限制，不另定一个数。
        over = [f"00000000-0000-4000-8000-{index:012d}" for index in range(library_browse_max() + 1)]
        self.assertEqual(self.batch(over).status_code, 400)
        self.assertIn("最多", self.batch(over).json()["error"])

    def test_the_library_count_moves_with_the_batch(self):
        rows = [self.publication() for _ in range(3)]
        before = Client().get("/api/library", HTTP_X_QB_REQUEST="1").json()["total"]
        self.assertEqual(before, 3)
        self.batch([row.id for row in rows])
        self.assertEqual(Client().get("/api/library", HTTP_X_QB_REQUEST="1").json()["total"], 0)


def library_browse_max():
    from . import library_browse
    return library_browse.MAX_BATCH_IDS

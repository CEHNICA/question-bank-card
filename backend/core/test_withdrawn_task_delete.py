"""撤回完的题不再锁住任务：题库里还有活题才拦删除，全撤了就能删。

以前这条保护数的是全部题库记录，撤回过的也算，于是「已经撤回了为什么还删不掉」。
删掉任务不会丢来源：PublishedQuestion.paper 是 SET_NULL，卷名字符串和题面快照
都抄在记录自己身上。
"""
from copy import deepcopy
import json

from django.test import TestCase

from . import library
from .models import Paper, PublishedQuestion
from .test_v110_types_origin import TempDataMixin


class WithdrawnThenDeleteTaskTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.paper.status = Paper.Status.READY
        self.paper.save(update_fields=["status"])
        self.questions = [self.card(self.paper, number=n, question_type="free_response", stem=f"计算 ${n}+1$。")
                          for n in (1, 2)]

    def approve(self, question):
        return self.client.post(f"/api/questions/{question.pk}/approve", json.dumps({"approved": True}),
                                content_type="application/json", HTTP_X_QB_REQUEST="1")

    def withdraw(self, publication):
        return self.client.post(f"/api/library/{publication.pk}/withdraw", json.dumps({}),
                                content_type="application/json", HTTP_X_QB_REQUEST="1")

    def delete_task(self):
        return self.client.delete(f"/api/papers/{self.paper.pk}", HTTP_X_QB_REQUEST="1")

    def test_a_live_bank_question_still_blocks_deleting_the_task(self):
        for question in self.questions:
            self.assertEqual(self.approve(question).status_code, 200)
        refused = self.delete_task()
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("正式题库", refused.json()["error"])
        # 说清楚还剩几道、下一步做什么，而不是丢一句「不能删除」
        self.assertIn("2", refused.json()["error"])
        self.assertIn("撤回", refused.json()["error"])
        self.assertTrue(Paper.objects.filter(pk=self.paper.pk).exists())

    def test_one_live_question_still_blocks_even_after_its_siblings_are_withdrawn(self):
        for question in self.questions:
            self.approve(question)
        first = self.questions[0].publications.get()
        self.assertEqual(self.withdraw(first).status_code, 200)
        refused = self.delete_task()
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("1", refused.json()["error"])
        self.assertTrue(Paper.objects.filter(pk=self.paper.pk).exists())

    def test_task_becomes_deletable_once_every_question_is_withdrawn(self):
        for question in self.questions:
            self.approve(question)
        for publication in list(PublishedQuestion.objects.filter(paper=self.paper)):
            self.assertEqual(self.withdraw(publication).status_code, 200)
        self.assertEqual(PublishedQuestion.objects.filter(paper=self.paper).count(), 2)
        self.assertEqual(
            PublishedQuestion.objects.filter(paper=self.paper, status=PublishedQuestion.Status.PUBLISHED).count(), 0)

        deleted = self.delete_task()
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertFalse(Paper.objects.filter(pk=self.paper.pk).exists())

    def test_deleting_the_task_keeps_the_withdrawn_snapshot_traceable(self):
        for question in self.questions:
            self.approve(question)
        publications = list(PublishedQuestion.objects.filter(paper=self.paper).order_by("number"))
        snapshots = [deepcopy(row.content) for row in publications]
        filenames = [row.source_filename for row in publications]
        for publication in publications:
            self.withdraw(publication)
        self.assertEqual(self.delete_task().status_code, 200)

        # 记录还在，题面快照和卷名都在，只是再也回不到已删掉的那份原卷。
        for publication, snapshot, filename in zip(publications, snapshots, filenames):
            publication.refresh_from_db()
            self.assertIsNone(publication.paper_id)
            self.assertIsNone(publication.question_id)
            self.assertEqual(publication.content, snapshot)
            self.assertEqual(publication.source_filename, filename)
            self.assertEqual(publication.status, PublishedQuestion.Status.WITHDRAWN)
        # 撤回过的题本来就不在题库列表里，删完任务列表也没有它。
        self.assertEqual(self.client.get("/api/library").json()["total"], 0)
        # 题卡也没了：它们跟着任务一起删。
        self.assertEqual(PublishedQuestion.objects.filter(question__isnull=False).count(), 0)

    def test_withdrawing_takes_the_question_out_of_the_bank_without_lying_about_it(self):
        """撤回之后题卡必须说实话：题库里没有这一版，就不能说「已通过并入库」。

        撤回只动题库那一条记录，「已通过」是另一个开关，要靠取消对号才清 ——
        所以这里分别断言两件事，不把「撤回」当成「撤销通过」。
        """
        for question in self.questions:
            self.approve(question)
        for publication in list(PublishedQuestion.objects.filter(paper=self.paper)):
            self.withdraw(publication)

        listing = self.client.get("/api/library").json()
        self.assertEqual((listing["total"], listing["items"]), (0, []))

        paper_json = self.client.get(f"/api/papers/{self.paper.pk}").json()
        counts = paper_json["paper"]["counts"]
        # 题库里没有 = 没入库 = 这道题和题库里那份不再一模一样。
        self.assertEqual((counts["published"], counts["settled"]), (0, 0))
        # 题卡上「题库里有这一版」必须为空：对号就是靠它才会显示成「已通过并入库」。
        self.assertEqual([question["publication"] for question in paper_json["questions"]], [None, None])
        # 「已通过」还留着，所以不算「要看」——那是取消对号才清的东西。
        self.assertEqual((counts["approved"], counts["todo"]), (2, 0))

        for question in self.questions:
            response = self.client.post(f"/api/questions/{question.pk}/approve",
                                        json.dumps({"approved": False}),
                                        content_type="application/json", HTTP_X_QB_REQUEST="1")
            self.assertEqual(response.status_code, 200, response.content)
        after = self.client.get(f"/api/papers/{self.paper.pk}").json()["paper"]["counts"]
        self.assertEqual((after["approved"], after["settled"], after["published"], after["todo"]), (0, 0, 0, 2))

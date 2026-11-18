"""统一回收站：已归档的资料 + 已下架的试题。

Eagle 工作台的回收站面板要一份「现在看不见、但还在库里」的总账：
左边点「🗑️ 回收站」就能看见哪些资料被归档了、哪些题被下架了，并把它们放回去。

之前这条链从头到尾是断的：
  1. 前端调 GET /api/library/trash，后端压根没有这条路由 —— 面板永远是空的；
  2. 前端所有回收站请求都没带 X-QB-Request 头，被守卫拦成 403 ——
     「移入回收站」从来没成功过，所以回收站连东西都没有；
  3. 恢复、彻底删除的四个端点（restore-batch / purge-batch / 单题 restore / 单题 purge）
     同样不存在，四个按钮点下去全是 404。

这里把整条链补齐，并钉死面板上真正要用的那些字段。
"""

from copy import deepcopy

from django.test import Client, TestCase

from . import library
from .models import Paper, PublishedQuestion, Question


class LibraryTrashTests(TestCase):
    def setUp(self):
        self.serial = 0
        self.client = Client()

    # ---- 造数据 ----
    def make_paper(self, *, filename="月考卷.pdf", archived=False, material_type="exam"):
        return Paper.objects.create(
            filename=filename, kind="pdf", sha256="c" * 64, material_type=material_type, archived=archived
        )

    def make_publication(self, *, paper=None, question=None, number=1, status="published", section="", version=1):
        self.serial += 1
        if question:
            paper = question.paper
        paper = paper or self.make_paper(filename=f"资料{self.serial}.pdf")
        question = question or Question.objects.create(
            paper=paper,
            number=self.serial,
            question_type="single_choice",
            stem=f"虚构题卡 {self.serial}",
            state="green",
        )
        content = deepcopy(library.final_content(question))
        content.update(document_id=str(paper.id), source_filename=paper.filename)
        if section:
            content["section"] = section
        # 原卷位置：页码是唯一认得出重号题的硬锚点，回收站也要带着。
        content["sources"] = [{"page_idx": 102, "bbox": [0, 0, 100, 100]}]
        return PublishedQuestion.objects.create(
            paper=paper,
            question=question,
            source_filename=paper.filename,
            number=number,
            question_type=question.question_type,
            version=version,
            status=status,
            content=content,
            content_hash=library.content_hash(content),
            review_source="human",
        )

    # ---- 请求帮手 ----
    def trash(self, **kwargs):
        return self.client.get("/api/library/trash", HTTP_X_QB_REQUEST="1", **kwargs)

    def post(self, url, payload=None):
        return self.client.post(
            url, payload if payload is not None else {}, content_type="application/json", HTTP_X_QB_REQUEST="1"
        )

    # ---- 1. 端点存在 ----
    def test_trash_endpoint_exists(self):
        """以前这条路由不存在，Eagle 面板拿到的是 404，然后 catch 掉，装作空的。"""
        response = self.trash()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn("archived_papers", body)
        self.assertIn("withdrawn_questions", body)
        self.assertIn("counts", body)

    def test_restore_and_purge_endpoints_exist(self):
        """面板上的四个按钮以前全是死路。"""
        restore_me = self.make_publication(status="withdrawn")
        purge_me = self.make_publication(status="withdrawn")
        self.assertEqual(self.post(f"/api/library/{restore_me.id}/restore").status_code, 200)
        self.assertEqual(self.post(f"/api/library/{purge_me.id}/purge").status_code, 200)
        self.assertEqual(self.post("/api/library/restore-batch", {"ids": [str(restore_me.id)]}).status_code, 200)
        self.assertEqual(self.post("/api/library/purge-batch", {"ids": [str(purge_me.id)]}).status_code, 200)

    # ---- 2. 面板要的东西都在 ----
    def test_trash_lists_archived_papers_and_withdrawn_questions(self):
        paper = self.make_paper(filename="（A版）必修第一册.pdf", archived=True, material_type="book")
        row = self.make_publication(paper=paper, number=1, status="withdrawn", section="习题3.4")
        live = self.make_publication(paper=paper, number=2, status="published")

        body = self.trash().json()
        self.assertEqual(len(body["archived_papers"]), 1)
        self.assertEqual(len(body["withdrawn_questions"]), 1)
        self.assertEqual(body["counts"], {"papers": 1, "questions": 1})

        entry = body["archived_papers"][0]
        self.assertEqual(entry["id"], str(paper.id))
        self.assertEqual(entry["material_type"], "book")
        self.assertTrue(entry["archived"])
        self.assertTrue(entry["archived_at"])
        # 这份卷里还有一道在库里 → 清空回收站时不能顺手把它删了。
        self.assertEqual(entry["published"], 1)

        item = body["withdrawn_questions"][0]
        self.assertEqual(item["id"], str(row.id))
        self.assertEqual(item["questionNumber"], 1)
        self.assertEqual(item["questionType"], "single_choice")
        self.assertEqual(item["documentId"], str(paper.id))
        self.assertEqual(item["paperName"], "（A版）必修第一册.pdf")
        self.assertTrue(item["stemPreview"])

    def test_trash_keeps_the_locator_fields(self):
        """回收站里也要说得清是哪道题 —— 重号的教材题光有题号认不出来。"""
        paper = self.make_paper(filename="教材.pdf", archived=True, material_type="book")
        row = self.make_publication(paper=paper, number=1, status="withdrawn", section="习题3.4")
        item = self.trash().json()["withdrawn_questions"][0]
        self.assertEqual(item["number"], 1)
        self.assertEqual(item["section"], "习题3.4")
        self.assertIsNotNone(item["page"])

    def test_live_questions_never_show_up_in_the_trash(self):
        self.make_publication(status="published")
        body = self.trash().json()
        self.assertEqual(body["withdrawn_questions"], [])
        self.assertEqual(body["counts"]["questions"], 0)

    # ---- 3. 放回去 ----
    def test_restore_batch_puts_questions_back_on_shelf(self):
        rows = [self.make_publication(number=index, status="withdrawn") for index in range(3)]
        response = self.post("/api/library/restore-batch", {"ids": [str(row.id) for row in rows]})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["restored_count"], 3)
        self.assertEqual(body["skipped"], [])
        self.assertEqual(PublishedQuestion.objects.filter(status="published").count(), 3)
        self.assertEqual(PublishedQuestion.objects.filter(status="withdrawn").count(), 0)
        # 放回去之后题库里就看得见了。
        self.assertEqual(self.client.get("/api/library", HTTP_X_QB_REQUEST="1").json()["total"], 3)

    def test_restore_single_publication(self):
        row = self.make_publication(status="withdrawn")
        body = self.post(f"/api/library/{row.id}/restore").json()
        self.assertTrue(body["ok"])
        self.assertEqual(PublishedQuestion.objects.get(pk=row.id).status, "published")

    def test_restore_refuses_a_question_whose_newer_version_is_already_published(self):
        """同一道原题已经有新版在库里了，再把旧版放回去会变成两份。"""
        paper = self.make_paper()
        question = Question.objects.create(
            paper=paper, number=1, question_type="single_choice", stem="原题", state="green"
        )
        old = self.make_publication(paper=paper, question=question, number=1, status="withdrawn", version=1)
        newer = self.make_publication(paper=paper, question=question, number=1, status="published", version=2)
        body = self.post("/api/library/restore-batch", {"ids": [str(old.id)]}).json()
        self.assertEqual(body["restored_count"], 0)
        self.assertEqual(len(body["skipped"]), 1)
        self.assertIn("新版本", body["skipped"][0]["reason"])
        self.assertEqual(PublishedQuestion.objects.get(pk=old.id).status, "withdrawn")
        self.assertEqual(PublishedQuestion.objects.get(pk=newer.id).status, "published")

    def test_restore_refuses_a_question_whose_paper_is_still_in_the_trash(self):
        """资料还在回收站里，题放回去也是看不见的 —— 说清楚，别装作放好了。"""
        paper = self.make_paper(archived=True)
        row = self.make_publication(paper=paper, status="withdrawn")
        body = self.post("/api/library/restore-batch", {"ids": [str(row.id)]}).json()
        self.assertEqual(body["restored_count"], 0)
        self.assertIn("资料", body["skipped"][0]["reason"])
        self.assertEqual(PublishedQuestion.objects.get(pk=row.id).status, "withdrawn")

    def test_restore_reports_every_id_it_refuses(self):
        missing = "00000000-0000-4000-8000-000000000000"
        row = self.make_publication(status="published")
        body = self.post("/api/library/restore-batch", {"ids": [missing, str(row.id)]}).json()
        self.assertEqual(body["restored_count"], 0)
        reasons = {item["id"]: item["reason"] for item in body["skipped"]}
        self.assertEqual(set(reasons), {missing, str(row.id)})

    # ---- 4. 彻底删掉 ----
    def test_purge_batch_deletes_for_good(self):
        rows = [self.make_publication(number=index, status="withdrawn") for index in range(3)]
        body = self.post("/api/library/purge-batch", {"ids": [str(row.id) for row in rows]}).json()
        self.assertEqual(body["purged_count"], 3)
        self.assertEqual(PublishedQuestion.objects.count(), 0)
        self.assertEqual(self.trash().json()["counts"]["questions"], 0)

    def test_purge_single_publication(self):
        row = self.make_publication(status="withdrawn")
        body = self.post(f"/api/library/{row.id}/purge").json()
        self.assertTrue(body["ok"])
        self.assertFalse(PublishedQuestion.objects.filter(pk=row.id).exists())

    def test_purge_never_touches_a_question_that_is_not_in_the_trash(self):
        """已上架的题不能被「彻底删除」顺手带走 —— 那不是回收站里的东西。"""
        row = self.make_publication(status="published")
        body = self.post("/api/library/purge-batch", {"ids": [str(row.id)]}).json()
        self.assertEqual(body["purged_count"], 0)
        self.assertTrue(PublishedQuestion.objects.filter(pk=row.id).exists())

    # ---- 5. 守卫 ----
    def test_every_entry_point_needs_the_page_guard(self):
        """改动必须来自本机题库页面 —— 跟题库里其它写接口同一条规矩。"""
        publication = self.make_publication(status="withdrawn")
        self.assertEqual(self.client.get("/api/library/trash").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/library/restore-batch", {"ids": [str(publication.id)]}, content_type="application/json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/library/purge-batch", {"ids": [str(publication.id)]}, content_type="application/json"
            ).status_code,
            403,
        )
        self.assertEqual(self.client.post(f"/api/library/{publication.id}/restore").status_code, 403)
        self.assertEqual(self.client.post(f"/api/library/{publication.id}/purge").status_code, 403)

    def test_write_endpoints_refuse_get(self):
        self.assertEqual(self.client.get("/api/library/restore-batch", HTTP_X_QB_REQUEST="1").status_code, 405)
        self.assertEqual(self.client.get("/api/library/purge-batch", HTTP_X_QB_REQUEST="1").status_code, 405)

    def test_single_recycle_endpoints_accept_empty_json_post_with_guard(self):
        """单条归档、恢复、撤回和彻底删除接口在携带 JSON 请求头与空对象时均正常执行并保持 415 守卫。"""
        live = self.make_publication(status="published")
        paper = live.paper
        paper.status = Paper.Status.READY
        paper.save(update_fields=["status"])

        self.assertEqual(self.client.post(f"/api/papers/{paper.id}/archive", HTTP_X_QB_REQUEST="1").status_code, 415)
        self.assertEqual(
            self.client.post(
                f"/api/papers/{paper.id}/archive", data="{}", content_type="application/json", HTTP_X_QB_REQUEST="1"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                f"/api/papers/{paper.id}/restore", data="{}", content_type="application/json", HTTP_X_QB_REQUEST="1"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                f"/api/library/{live.id}/withdraw", data="{}", content_type="application/json", HTTP_X_QB_REQUEST="1"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                f"/api/library/{live.id}/restore", data="{}", content_type="application/json", HTTP_X_QB_REQUEST="1"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                f"/api/library/{live.id}/withdraw", data="{}", content_type="application/json", HTTP_X_QB_REQUEST="1"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                f"/api/library/{live.id}/purge", data="{}", content_type="application/json", HTTP_X_QB_REQUEST="1"
            ).status_code,
            200,
        )

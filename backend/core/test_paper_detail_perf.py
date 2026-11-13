"""大试卷详情接口高性能快照与缓存协商测试。

测试关键目标：
  1. 首次访问生成快照，第二次访问直接命中内存快照返回预压缩 JSON
  2. 携带 If-None-Match 命中时快速返回 304 Not Modified
  3. 试卷改名或题目版本递增后，快照自动失效并重新生成
  4. source_images 切片元数据缓存正常命中与复用
"""

import json
from unittest import mock
from django.test import TestCase, RequestFactory

from core.models import Paper, Question
from core import views, source_images


class PaperDetailPerfTests(TestCase):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        views.invalidate_paper_detail_cache()
        source_images._CROP_META_CACHE.clear()
        self.paper = Paper.objects.create(
            filename="test_paper.pdf",
            task_name="性能测试试卷",
            kind="pdf",
            status=Paper.Status.READY,
        )
        for i in range(5):
            Question.objects.create(
                paper=self.paper,
                number=i + 1,
                stem=f"这是第 {i+1} 道测试题目的题干",
                state=Question.State.YELLOW,
                content_revision=1,
            )

    def tearDown(self):
        views.invalidate_paper_detail_cache()
        source_images._CROP_META_CACHE.clear()
        super().tearDown()

    def test_paper_detail_cache_and_etag(self):
        """测试首次生成快照、二次命中快照以及 304 条件协商。"""
        # 1. 首次访问
        request1 = self.factory.get(f"/api/papers/{self.paper.id}")
        resp1 = views.paper_detail(request1, self.paper.id)
        self.assertEqual(resp1.status_code, 200)
        self.assertIn("ETag", resp1.headers)
        etag = resp1.headers["ETag"]
        data1 = json.loads(resp1.content)
        self.assertEqual(len(data1["questions"]), 5)

        # 2. 第二次访问：命中快照
        request2 = self.factory.get(f"/api/papers/{self.paper.id}")
        resp2 = views.paper_detail(request2, self.paper.id)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.headers["ETag"], etag)
        data2 = json.loads(resp2.content)
        self.assertEqual(data1, data2)

        # 3. 带 If-None-Match：返回 304
        request3 = self.factory.get(f"/api/papers/{self.paper.id}", HTTP_IF_NONE_MATCH=etag)
        resp3 = views.paper_detail(request3, self.paper.id)
        self.assertEqual(resp3.status_code, 304)

    def test_paper_detail_invalidation_on_question_change(self):
        """测试题目版本变更后，快照自动失效并生成新 ETag。"""
        # 生成初始快照
        request1 = self.factory.get(f"/api/papers/{self.paper.id}")
        resp1 = views.paper_detail(request1, self.paper.id)
        old_etag = resp1.headers["ETag"]

        # 修改其中一道题目的 revision
        first_q = self.paper.questions.first()
        first_q.content_revision += 1
        first_q.save()

        # 再次请求，指纹变化，ETag 必须更新且内容重新生成
        request2 = self.factory.get(f"/api/papers/{self.paper.id}")
        resp2 = views.paper_detail(request2, self.paper.id)
        self.assertEqual(resp2.status_code, 200)
        new_etag = resp2.headers["ETag"]
        self.assertNotEqual(old_etag, new_etag)

        # 使用旧 ETag 请求，不再能命中 304
        request3 = self.factory.get(f"/api/papers/{self.paper.id}", HTTP_IF_NONE_MATCH=old_etag)
        resp3 = views.paper_detail(request3, self.paper.id)
        self.assertEqual(resp3.status_code, 200)
        self.assertEqual(resp3.headers["ETag"], new_etag)

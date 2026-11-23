"""原卷的页码清单：核验证据要能翻到答案印在下一页的那一页。

``/api/papers/<id>`` 带着 structure、question_groups、counts 走一遍，
一份 270 页的教材实测 1.4 MB、首次还要 1.6 秒 —— 只为了知道「这份原卷有多少页」
不值当。翻页控件只需要页码清单，所以单开一个只读接口。

口径必须和 ``page_preview`` 一致：能预览的页，才是可以翻到的页。
"""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

from django.test import Client, TestCase, override_settings

from .models import Paper


class PaperPageIndexTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        override = override_settings(DATA_ROOT=Path(temp.name))
        override.enable()
        self.addCleanup(override.disable)
        self.paper = Paper.objects.create(
            filename="pages.pdf",
            kind="pdf",
            sha256="e" * 64,
            source_path=str(Path(temp.name) / "pages.pdf"),
            render_path=str(Path(temp.name) / "pages.pdf"),
            pages=[{"page_idx": index, "width": 595, "height": 842} for index in range(3)],
            status=Paper.Status.READY,
            structure={"sections": [{"title": f"第{index}章", "pages": [0]} for index in range(200)]},
        )
        self.client = Client()

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_lists_every_page_of_the_original(self):
        body = self.get(f"/api/papers/{self.paper.id}/pages")
        self.assertEqual(body["pages"], [0, 1, 2])
        self.assertEqual(body["count"], 3)
        self.assertEqual(body["paper_id"], str(self.paper.id))

    def test_documents_alias_serves_the_same_list(self):
        self.assertEqual(
            self.get(f"/api/documents/{self.paper.id}/pages")["pages"],
            self.get(f"/api/papers/{self.paper.id}/pages")["pages"],
        )

    def test_payload_stays_small(self):
        body = self.get(f"/api/papers/{self.paper.id}/pages")
        self.assertLess(len(json.dumps(body, ensure_ascii=False)), 4096)
        for heavy in ("structure", "question_groups", "counts", "processing_plan"):
            self.assertNotIn(heavy, body)

    def test_missing_paper_is_a_404(self):
        response = self.client.get(f"/api/papers/{uuid.uuid4()}/pages")
        self.assertEqual(response.status_code, 404)

    def test_it_is_read_only(self):
        response = self.client.post(
            f"/api/papers/{self.paper.id}/pages",
            data="{}",
            content_type="application/json",
            headers={"X-QB-Request": "1"},
        )
        self.assertEqual(response.status_code, 405)

    def test_broken_page_entries_are_skipped(self):
        self.paper.pages = [
            {"page_idx": 2, "width": 595, "height": 842},
            {"page_idx": 0, "width": 595, "height": 842},
            {"width": 595},
            "坏了",
        ]
        self.paper.save(update_fields=["pages"])
        body = self.get(f"/api/papers/{self.paper.id}/pages")
        self.assertEqual(body["pages"], [0, 2])
        self.assertEqual(body["count"], 2)

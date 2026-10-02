"""Isolated, offline library pagination, ordered batches and atomic named paper drafts."""

from __future__ import annotations

import json
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import path
from django.utils import timezone

from . import library, library_browse, library_drafts
from .models import Paper, PublishedQuestion, Question

urlpatterns = [
    path("api/library", library_browse.library_list),
    path("api/library/batch", library_browse.library_batch),
    path("api/library/drafts", library_drafts.drafts_view),
    path("api/library/drafts/<uuid:draft_id>", library_drafts.draft_detail),
]


@override_settings(ROOT_URLCONF=__name__)
class LibraryBrowseDraftTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        settings = override_settings(DATA_ROOT=self.root)
        settings.enable()
        self.addCleanup(settings.disable)
        self.client = Client()
        self.serial = 0

    def publication(self, *, source="甲卷.pdf", paper=None, question=None, number=None,
                    kind="single_choice", answer="", review="human", tags=(), version=1,
                    status="published", stem="虚构题：若 $x+1=3$，求 $x$。"):
        self.serial += 1
        paper = paper or (question.paper if question else None) or Paper.objects.create(
            filename=source, kind="pdf", sha256="a" * 64)
        question = question or Question.objects.create(paper=paper, number=number or self.serial,
                                                       question_type=kind, stem=stem, state="green")
        content = {"stem": stem, "options": {}, "answer": answer, "analysis": "", "figures": [],
                   "origin": "虚构出处", "source_filename": source, "document_id": str(paper.id),
                   "question_type": kind, "sources": []}
        return PublishedQuestion.objects.create(
            paper=paper, question=question, number=number or self.serial, source_filename=source,
            question_type=kind, version=version, status=status, review_source=review, content=content,
            content_hash=library.content_hash(content), search_text=library.search_key(stem),
            extras={"tags": list(tags)}, tags_text=library.tags_text(tags))

    def mutate(self, method, url, body=None, *, header=True, content_type="application/json"):
        return getattr(self.client, method)(url, data=json.dumps(body or {}), content_type=content_type,
                                           **({"HTTP_X_QB_REQUEST": "1"} if header else {}))

    def create_draft(self, **changes):
        return self.mutate("post", "/api/library/drafts", {"title": "单元练习", "ids": [], **changes})

    def test_recent_and_source_sorts_have_stable_pagination(self):
        first = self.publication(source="乙卷.pdf", number=2)
        second = self.publication(source="甲卷.pdf", number=1)
        third = self.publication(source="乙卷.pdf", number=1)
        for index, item in enumerate((first, second, third)):
            PublishedQuestion.objects.filter(pk=item.id).update(published_at=timezone.now() + timedelta(seconds=index))
        recent = self.client.get("/api/library", {"limit": 2}).json()
        self.assertEqual([item["id"] for item in recent["items"]], [str(third.id), str(second.id)])
        self.assertEqual((recent["total"], recent["sort"], recent["next_offset"]), (3, "recent", 2))
        tail = self.client.get("/api/library", {"limit": 2, "offset": 2}).json()
        self.assertEqual([item["id"] for item in tail["items"]], [str(first.id)])
        self.assertFalse(tail["has_more"])
        self.assertIsNone(tail["next_offset"])
        by_source = self.client.get("/api/library", {"sort": "source"}).json()
        expected = sorted((first, second, third), key=lambda item: (item.source_filename, item.number))
        self.assertEqual([item["id"] for item in by_source["items"]], [str(item.id) for item in expected])

    def test_default_page_has_forty_items(self):
        for _ in range(41):
            self.publication()
        page = self.client.get("/api/library").json()
        self.assertEqual((page["total"], len(page["items"]), page["limit"], page["next_offset"]), (41, 40, 40, 40))
        self.assertEqual(len(self.client.get("/api/library", {"offset": 40}).json()["items"]), 1)

    def test_facets_keep_other_conditions_and_omit_their_own_filter(self):
        first = self.publication(kind="fill_in_blank", tags=["函数"])
        self.publication(paper=first.paper, answer="2", review="ai", tags=["不等式"])
        self.publication(source="乙卷.pdf", answer="3", tags=["函数"])
        self.publication(source="撤回卷.pdf", kind="free_response", status="withdrawn")
        body = self.client.get("/api/library", {"document": str(first.paper_id), "type": "fill_in_blank"}).json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["facets"]["types"], {"single_choice": 1, "fill_in_blank": 1})
        self.assertEqual(body["facets"]["sources"][0]["count"], 1)
        answered = self.client.get("/api/library", {"document": str(first.paper_id), "answer": "no"}).json()
        self.assertEqual(answered["facets"]["answers"], {"yes": 1, "no": 1})
        self.assertNotIn("free_response", answered["facets"]["types"])

    def test_search_review_and_exact_tags_still_filter_the_library(self):
        tagged = self.publication(stem="虚构题：集合 $A$", tags=["集合间的基本关系"], review="ai")
        self.publication(stem="另一道题", tags=["集合"])
        body = self.client.get("/api/library", {"q": "集合", "tag": "集合间的基本关系", "review": "ai"}).json()
        self.assertEqual([item["id"] for item in body["items"]], [str(tagged.id)])
        self.assertEqual(self.client.get("/api/library", {"document": "invalid"}).json()["total"], 0)

    def test_invalid_sort_and_paging_are_rejected(self):
        for params in ({"sort": "random"}, {"limit": "x"}, {"offset": "x"}):
            self.assertEqual(self.client.get("/api/library", params).status_code, 400)
        self.assertEqual(self.client.post("/api/library").status_code, 405)

    def test_batch_preserves_order_and_reports_each_unavailable_snapshot(self):
        live = self.publication()
        old = self.publication(status="superseded")
        replacement = self.publication(question=old.question, version=2)
        withdrawn = self.publication(status="withdrawn")
        other = self.publication()
        absent = str(uuid.uuid4())
        before = list(PublishedQuestion.objects.values())
        ids = [str(other.id), str(old.id), absent, str(live.id).upper(), str(withdrawn.id), str(other.id)]
        body = self.mutate("post", "/api/library/batch", {"ids": ids}).json()
        self.assertEqual([item["id"] for item in body["items"]], [str(other.id), str(live.id)])
        self.assertEqual([item["id"] for item in body["missing"]], [str(old.id), absent, str(withdrawn.id)])
        self.assertEqual([item["reason"] for item in body["missing"]], ["superseded", "not_found", "withdrawn"])
        self.assertEqual(body["missing"][0]["replacement_id"], str(replacement.id))
        self.assertNotIn(str(replacement.id), [item["id"] for item in body["items"]])
        self.assertEqual(len(body["requested_ids"]), 5)
        self.assertEqual(list(PublishedQuestion.objects.values()), before)

    def test_empty_batch_and_invalid_requests(self):
        self.assertEqual(self.mutate("post", "/api/library/batch", {"ids": []}).json()["items"], [])
        for ids in ("not-a-list", [False], ["bad"], [str(uuid.uuid4())] * 501):
            self.assertEqual(self.mutate("post", "/api/library/batch", {"ids": ids}).status_code, 400)
        self.assertEqual(self.mutate("post", "/api/library/batch", {"ids": [], "minimax_key": "fake"}).status_code, 400)
        self.assertEqual(self.mutate("post", "/api/library/batch", {"ids": []}, header=False).status_code, 403)
        self.assertEqual(self.mutate("post", "/api/library/batch", {"ids": []}, content_type="text/plain").status_code, 415)
        self.assertEqual(self.client.get("/api/library/batch").status_code, 405)

    def test_draft_crud_keeps_order_and_safe_print_defaults(self):
        first, second = self.publication(), self.publication()
        created = self.create_draft(ids=[str(second.id), str(first.id)])
        self.assertEqual(created.status_code, 201)
        draft = created.json()["draft"]
        self.assertEqual(draft["ids"], [str(second.id), str(first.id)])
        self.assertEqual(draft["print_options"], library_drafts.PRINT_DEFAULTS)
        self.assertTrue(draft["validity"]["valid"])
        listing = self.client.get("/api/library/drafts").json()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["drafts"][0]["id"], draft["id"])
        url = f'/api/library/drafts/{draft["id"]}'
        updated = self.mutate("put", url, {"title": "期末练习", "revision": 1,
                                         "print_options": {"origin": True}}).json()["draft"]
        self.assertEqual((updated["title"], updated["revision"]), ("期末练习", 2))
        self.assertEqual(updated["ids"], draft["ids"])
        self.assertFalse(updated["print_options"]["ai_answers"])
        self.assertEqual(self.client.get(url).json()["draft"], updated)
        self.assertEqual(self.mutate("delete", url).json(), {"deleted": draft["id"]})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_revision_conflict_never_overwrites_another_windows_changes(self):
        draft = self.create_draft().json()["draft"]
        url = f'/api/library/drafts/{draft["id"]}'
        self.assertEqual(self.mutate("put", url, {"title": "窗口甲", "revision": 1}).status_code, 200)
        original = library_drafts.draft_path().read_bytes()
        self.assertEqual(self.mutate("put", url, {"title": "窗口乙", "revision": 1}).status_code, 409)
        self.assertEqual(library_drafts.draft_path().read_bytes(), original)
        self.assertEqual(self.client.get(url).json()["draft"]["title"], "窗口甲")

    def test_draft_validity_changes_without_rewriting_saved_ids_or_questions(self):
        selected = self.publication()
        draft = self.create_draft(ids=[str(selected.id)]).json()["draft"]
        before_file = library_drafts.draft_path().read_bytes()
        selected.status = "superseded"
        selected.save(update_fields=["status"])
        replacement = self.publication(question=selected.question, version=2)
        body = self.client.get(f'/api/library/drafts/{draft["id"]}').json()["draft"]
        self.assertFalse(body["validity"]["valid"])
        self.assertEqual(body["ids"], [str(selected.id)])
        self.assertEqual(body["validity"]["missing"][0]["replacement_id"], str(replacement.id))
        self.assertEqual(library_drafts.draft_path().read_bytes(), before_file)
        selected.delete()
        body = self.client.get(f'/api/library/drafts/{draft["id"]}').json()["draft"]
        self.assertEqual(body["validity"]["missing"][0]["reason"], "not_found")

    def test_strict_draft_fields_reject_content_credentials_and_bad_switches(self):
        for changes in ({"title": ""}, {"title": "a" * 121}, {"title": "名称\n换行"},
                        {"ids": [str(uuid.uuid4())] * 501}, {"ids": [1]},
                        {"content": {"stem": "不能写题面"}}, {"minimax_key": "fake"},
                        {"print_options": {"answers": 1}}, {"print_options": {"key": "fake"}},
                        {"revision": True}):
            self.assertEqual(self.create_draft(**changes).status_code, 400)
        self.assertFalse(library_drafts.draft_path().exists())

    def test_mutations_require_local_request_header_and_json(self):
        self.assertEqual(self.mutate("post", "/api/library/drafts", {"ids": []}, header=False).status_code, 403)
        self.assertEqual(self.mutate("post", "/api/library/drafts", {"ids": []}, content_type="text/plain").status_code, 415)
        draft = self.create_draft().json()["draft"]
        for method in ("put", "delete"):
            self.assertEqual(self.mutate(method, f'/api/library/drafts/{draft["id"]}', header=False).status_code, 403)

    def test_unreadable_store_is_not_replaced_by_an_empty_one(self):
        target = library_drafts.draft_path()
        original = b'{"schema":1,"drafts":broken}'
        target.write_bytes(original)
        self.assertEqual(self.client.get("/api/library/drafts").status_code, 409)
        self.assertEqual(self.create_draft().status_code, 409)
        self.assertEqual(target.read_bytes(), original)

    def test_stored_unknown_fields_are_refused_without_returning_them(self):
        draft = self.create_draft().json()["draft"]
        target = library_drafts.draft_path()
        data = json.loads(target.read_text(encoding="utf-8"))
        data["drafts"][0]["private_key"] = "fake-never-return"
        original = json.dumps(data).encode()
        target.write_bytes(original)
        response = self.client.get("/api/library/drafts")
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("fake-never-return", response.content.decode())
        self.assertEqual(self.mutate("put", f'/api/library/drafts/{draft["id"]}', {"title": "新名"}).status_code, 409)
        self.assertEqual(target.read_bytes(), original)

    def test_failed_atomic_replace_preserves_previous_draft_and_removes_temp_file(self):
        draft = self.create_draft().json()["draft"]
        original = library_drafts.draft_path().read_bytes()
        with mock.patch.object(library_drafts.os, "replace", side_effect=OSError("simulated write failure")):
            response = self.mutate("put", f'/api/library/drafts/{draft["id"]}', {"title": "未保存"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(library_drafts.draft_path().read_bytes(), original)
        self.assertEqual(list(self.root.glob(".library-drafts-*.json")), [])

    def test_store_count_and_byte_limits_are_enforced_without_overwriting(self):
        with mock.patch.object(library_drafts, "MAX_DRAFTS", 1):
            self.assertEqual(self.create_draft().status_code, 201)
            original = library_drafts.draft_path().read_bytes()
            self.assertEqual(self.create_draft(title="第二份").status_code, 409)
            self.assertEqual(library_drafts.draft_path().read_bytes(), original)
        with mock.patch.object(library_drafts, "MAX_STORE_BYTES", 50):
            self.assertEqual(self.create_draft().status_code, 409)
            self.assertEqual(library_drafts.draft_path().read_bytes(), original)

    def test_simultaneous_saves_do_not_lose_drafts(self):
        def save(index):
            return library_drafts._save({"title": f"草稿{index}", "ids": []})["id"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(save, range(8)))
        stored = library_drafts._read()["drafts"]
        self.assertEqual({item["id"] for item in stored}, set(ids))
        self.assertEqual(len(stored), 8)

    def test_list_empty_and_missing_draft_do_not_create_store(self):
        self.assertEqual(self.client.get("/api/library/drafts").json(), {"drafts": [], "total": 0})
        self.assertEqual(self.client.get(f"/api/library/drafts/{uuid.uuid4()}").status_code, 404)
        self.assertFalse(library_drafts.draft_path().exists())

    def test_draft_list_is_sorted_by_latest_save(self):
        first = self.create_draft(title="甲").json()["draft"]
        second = self.create_draft(title="乙").json()["draft"]
        self.assertEqual([item["id"] for item in self.client.get("/api/library/drafts").json()["drafts"]],
                         [second["id"], first["id"]])
        self.mutate("put", f'/api/library/drafts/{first["id"]}', {"title": "甲更新"})
        self.assertEqual(self.client.get("/api/library/drafts").json()["drafts"][0]["id"], first["id"])

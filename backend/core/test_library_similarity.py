"""Stage 3 tests: local offline dedup, multi-source handwritten evidence archiving, and variant groups."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from . import library, library_similarity
from .models import LibraryTag, LibraryTagAssignment, Paper, PublishedQuestion, Question


def _create_ready_paper_with_page(data_root: Path, filename: str = "周测卷.pdf") -> Paper:
    paper = Paper.objects.create(
        filename=filename, status=Paper.Status.READY, pages=[{"page_idx": 0, "width": 1000, "height": 1400}]
    )
    pages_dir = data_root / str(paper.pk) / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (800, 1120), (255, 255, 255))
    # Draw dark strokes simulating student handwriting so image is non-trivial.
    for x in range(120, 420):
        img.putpixel((x, 220), (20, 40, 180))
    img.save(pages_dir / "page_0.png", format="PNG")
    return paper


def _approve_and_publish(q: Question) -> tuple[PublishedQuestion, bool]:
    library.approve(q, now=timezone.now(), source="human")
    q.save()
    return library.publish(q, queue_enrichment=False)


def _create_published_question(
    paper: Paper,
    *,
    number: int = 1,
    stem: str = "",
    options: dict[str, str] | None = None,
    question_type: str = "free_response",
    answer: str = "",
) -> PublishedQuestion:
    q = Question.objects.create(
        paper=paper,
        number=number,
        question_type=question_type,
        body_mode="text",
        stem=stem,
        options=options or {},
        answer=answer,
        regions=[{"page_idx": 0, "bbox": [50, 80, 920, 320]}],
        regions_auto=[{"page_idx": 0, "bbox": [50, 80, 920, 320]}],
        figure_review={"status": "ok"},
        state=Question.State.GREEN,
    )
    pub, _created = _approve_and_publish(q)
    return pub


class LibrarySimilarityTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self._tmp.name)
        self._override = override_settings(DATA_ROOT=self.data_root)
        self._override.enable()
        library_similarity.bump_similarity_revision()

    def tearDown(self) -> None:
        library_similarity.bump_similarity_revision()
        self._override.disable()
        self._tmp.cleanup()

    def test_offline_duplicate_variant_classification_and_source_image_skip(self) -> None:
        paper_lib = _create_ready_paper_with_page(self.data_root, "期中真题.pdf")
        pub_base = _create_published_question(
            paper_lib,
            number=1,
            stem=r"设函数 $f(x)=\dfrac{1}{2}x^2-2ax+3$，若对任意 $x\in[1,4]$ 都有 $f(x)\leqslant 0$，求 $a$。",
            answer=r"$a \ge 2$",
        )

        paper_in = _create_ready_paper_with_page(self.data_root, "学生手写错题.pdf")
        q_dup = Question.objects.create(
            paper=paper_in,
            number=5,
            question_type="free_response",
            body_mode="text",
            stem=r"（2025·海淀期中）设函数$f(x)=\frac{1}{2}x^2-2ax+3$,若对任意$x\in[1,4]$都有$f(x)\le 0$,求$a$.",
            regions=[{"page_idx": 0, "bbox": [40, 120, 900, 340]}],
            figure_review={"status": "ok"},
            state=Question.State.GREEN,
        )
        q_var = Question.objects.create(
            paper=paper_in,
            number=6,
            question_type="free_response",
            body_mode="text",
            stem=r"设函数 $f(x)=\dfrac{1}{3}x^2-5ax+7$，若对任意 $x\in[2,6]$ 都有 $f(x)\leqslant 0$，求 $a$。",
            regions=[{"page_idx": 0, "bbox": [40, 360, 900, 560]}],
            figure_review={"status": "ok"},
            state=Question.State.GREEN,
        )
        q_img = Question.objects.create(
            paper=paper_in,
            number=7,
            question_type="unknown",
            body_mode="source_image",
            stem="",
            regions=[{"page_idx": 0, "bbox": [40, 580, 900, 760]}],
            state=Question.State.GREEN,
        )

        matches = library_similarity.find_matches_for_paper(paper_in)
        self.assertEqual(matches[q_dup.pk][0]["publication_id"], str(pub_base.pk))
        self.assertEqual(matches[q_dup.pk][0]["kind"], "duplicate")
        self.assertGreaterEqual(matches[q_dup.pk][0]["score"], 0.99)

        self.assertEqual(matches[q_var.pk][0]["publication_id"], str(pub_base.pk))
        self.assertEqual(matches[q_var.pk][0]["kind"], "variant")

        # Pure source_image without OCR text must never match or invoke OCR/AI.
        self.assertNotIn(q_img.pk, matches)

    def test_review_merge_archives_handwritten_crop_and_preserves_through_edits(self) -> None:
        paper_lib = _create_ready_paper_with_page(self.data_root, "空白原卷.pdf")
        pub = _create_published_question(paper_lib, number=1, stem="已知集合 $A=\\{1,2,3\\}$，求子集个数。", answer="8")

        paper_hw = _create_ready_paper_with_page(self.data_root, "李四错题拍照.pdf")
        q_hw = Question.objects.create(
            paper=paper_hw,
            number=2,
            question_type="free_response",
            body_mode="text",
            stem="已知集合 $A=\\{1,2,3\\}$，求子集个数。",
            regions=[{"page_idx": 0, "bbox": [40, 100, 900, 340]}],
            figure_review={"status": "ok"},
            state=Question.State.GREEN,
        )

        # GET /api/papers/<id> should include similarity_matches
        detail_res = self.client.get(f"/api/papers/{paper_hw.pk}")
        self.assertEqual(detail_res.status_code, 200)
        q_payload = detail_res.json()["questions"][0]
        self.assertEqual(q_payload["similarity_matches"][0]["publication_id"], str(pub.pk))

        merge_res = self.client.post(
            f"/api/questions/{q_hw.pk}/merge-duplicate",
            data=json.dumps({"target_publication_id": str(pub.pk)}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(merge_res.status_code, 200, merge_res.content)
        body = merge_res.json()
        self.assertEqual(PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).count(), 1)
        ev = body["evidence"]
        self.assertTrue(ev["crop_file"].startswith("evidence-"))
        crop_path = self.data_root / "library" / str(pub.pk) / ev["crop_file"]
        self.assertTrue(crop_path.is_file())

        # Evidence crop route returns HTTP 200 PNG
        img_res = self.client.get(ev["crop_url"])
        self.assertEqual(img_res.status_code, 200)
        self.assertEqual(img_res["Content-Type"], "image/png")
        self.assertGreater(len(b"".join(img_res.streaming_content)), 20)
        img_res.close()

        # save_extras and republish (carried_extras) both keep evidence_sources intact
        pub.refresh_from_db()
        library.save_extras(pub, {"custom_note": "kept"})
        pub.refresh_from_db()
        self.assertEqual(len(pub.extras.get("evidence_sources") or []), 1)

        orig_q = pub.question
        assert orig_q is not None
        orig_q.stem = "已知集合 $A=\\{1,2,3\\}$，求非空真子集与全部子集个数。"
        orig_q.edited = True
        orig_q.content_revision += 1
        new_pub, created = _approve_and_publish(orig_q)
        self.assertTrue(created)
        carried_evs = new_pub.extras.get("evidence_sources") or []
        self.assertEqual(len(carried_evs), 1)
        new_crop = self.data_root / "library" / str(new_pub.pk) / carried_evs[0]["crop_file"]
        self.assertTrue(new_crop.is_file())

        # Undo merge restores q_hw back to active review cards cleanly
        restore_res = self.client.post(
            f"/api/papers/{paper_hw.pk}/question-trash/{body['undo_batch_id']}/restore",
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(restore_res.status_code, 200)
        self.assertEqual(Question.objects.filter(pk=q_hw.pk).count(), 1)

    def test_review_preference_and_eagle_dedup_variants(self) -> None:
        paper1 = _create_ready_paper_with_page(self.data_root, "一模.pdf")
        pub1 = _create_published_question(
            paper1, number=1, stem="若函数 $f(x)=x^2-2mx+3$ 在 $[1,4]$ 上单调递增，求 $m$ 的取值范围。"
        )

        # Duplicate published card with a formal tag to merge inside /eagle
        paper_dup = _create_ready_paper_with_page(self.data_root, "二模错题.pdf")
        pub_dup = _create_published_question(
            paper_dup,
            number=3,
            stem="（2026·模拟）若函数 $f(x)=x^2-2mx+3$ 在 $[1,4]$ 上单调递增,求 $m$ 的取值范围。",
            answer="$(-\\infty, 1]$",
        )
        tag = LibraryTag.objects.create(name="二次函数单调性", kind=LibraryTag.Kind.KNOWLEDGE)
        LibraryTagAssignment.objects.create(publication=pub_dup, tag=tag, needs_review=False)

        scan_res = self.client.get("/api/library/dedup/scan")
        self.assertEqual(scan_res.status_code, 200)
        scan = scan_res.json()
        self.assertTrue(any(len(g["items"]) >= 2 for g in scan["duplicate_groups"]))

        merge_res = self.client.post(
            "/api/library/dedup/merge",
            data=json.dumps({"keep_id": str(pub1.pk), "merge_ids": [str(pub_dup.pk)]}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(merge_res.status_code, 200)
        pub_dup.refresh_from_db()
        pub1.refresh_from_db()
        self.assertEqual(pub_dup.status, PublishedQuestion.Status.WITHDRAWN)
        self.assertEqual(len(pub1.extras.get("evidence_sources") or []), 1)
        self.assertIn("二次函数单调性", library.tags_of(pub1.extras))
        self.assertEqual(pub1.content.get("answer"), "$(-\\infty, 1]$")

        # Detach evidence removes tab & PNG cleanly
        ev_id = pub1.extras["evidence_sources"][0]["id"]
        ev_file = pub1.extras["evidence_sources"][0]["crop_file"]
        del_res = self.client.delete(f"/api/library/{pub1.pk}/evidence/{ev_id}", HTTP_X_QB_REQUEST="1")
        self.assertEqual(del_res.status_code, 200)
        self.assertFalse((self.data_root / "library" / str(pub1.pk) / ev_file).exists())

        # Review card marks similarity-preference = bind_variant -> auto-binds on publish
        paper_var = _create_ready_paper_with_page(self.data_root, "变式练习.pdf")
        q_var = Question.objects.create(
            paper=paper_var,
            number=1,
            question_type="free_response",
            body_mode="text",
            stem="若函数 $f(x)=x^2-6mx+9$ 在 $[2,5]$ 上单调递增，求 $m$ 的取值范围。",
            regions=[{"page_idx": 0, "bbox": [40, 100, 900, 320]}],
            figure_review={"status": "ok"},
            state=Question.State.GREEN,
        )
        pref_res = self.client.post(
            f"/api/questions/{q_var.pk}/similarity-preference",
            data=json.dumps({"target_publication_id": str(pub1.pk), "mode": "bind_variant"}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(pref_res.status_code, 200)
        q_var.refresh_from_db()
        pub_var, _ = _approve_and_publish(q_var)

        list_res = self.client.get("/api/library?limit=20&slim=1")
        self.assertEqual(list_res.status_code, 200)
        by_id = {item["id"]: item for item in list_res.json()["items"]}
        self.assertIsNotNone(by_id[str(pub1.pk)]["variant_group_id"])
        self.assertEqual(by_id[str(pub1.pk)]["variant_group_id"], by_id[str(pub_var.pk)]["variant_group_id"])
        self.assertEqual(by_id[str(pub1.pk)]["variant_peers"][0]["id"], str(pub_var.pk))

        # Unbind cleans singleton group cleanly
        unbind_res = self.client.post(
            "/api/library/variants/unbind",
            data=json.dumps({"publication_id": str(pub_var.pk)}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(unbind_res.status_code, 200)
        pub1.refresh_from_db()
        pub_var.refresh_from_db()
        self.assertNotIn("variant_group_id", pub1.extras or {})
        self.assertNotIn("variant_group_id", pub_var.extras or {})

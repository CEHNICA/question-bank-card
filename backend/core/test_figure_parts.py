"""A table cut by a page break can be joined back into one figure."""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image, ImageDraw

from . import imaging, library
from .models import Paper, PublishedQuestion, Question
from .pipeline import paper_dir

TOP = [100, 800, 900, 990]      # the header and 甲车间 row at the foot of page 1
BOTTOM = [100, 10, 900, 60]     # the 乙车间 row at the top of page 2


def ruled_page(line_x: int, size=(1000, 1000)) -> Image.Image:
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).line([(line_x, 0), (line_x, size[1])], fill="black", width=2)
    return image


class StackPiecesTests(SimpleTestCase):
    def test_columns_line_up_and_halves_meet(self):
        page = ruled_page(500)
        joined = imaging.stack_figure_pieces([
            (page, imaging.to_pixels([100, 800, 900, 990], page.size)),
            # The lower half was framed a little wider; its column must not shift.
            (page, imaging.to_pixels([80, 10, 920, 60], page.size)),
        ])
        self.assertEqual(joined.height, 190 + 50)
        self.assertEqual(joined.width, 840)
        # The ruling at x=500 sits at the same canvas column in both halves.
        column = 500 - 80
        self.assertLess(joined.getpixel((column, 10))[0], 128)
        self.assertLess(joined.getpixel((column, 200))[0], 128)

    def test_page_of_another_width_is_scaled_to_the_first(self):
        first, second = ruled_page(500), ruled_page(1000, size=(2000, 2000))
        joined = imaging.stack_figure_pieces([
            (first, imaging.to_pixels(TOP, first.size)),
            (second, imaging.to_pixels(BOTTOM, second.size)),
        ])
        self.assertEqual(joined.width, 800)
        self.assertEqual(joined.height, 190 + 50)


class FigurePartsApiTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        override = override_settings(DATA_ROOT=Path(temp.name))
        override.enable()
        self.addCleanup(override.disable)
        self.paper = Paper.objects.create(
            filename="k2.pdf", kind="pdf", sha256="c" * 64,
            source_path=str(Path(temp.name) / "k2.pdf"), render_path=str(Path(temp.name) / "k2.pdf"),
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}, {"page_idx": 1, "width": 1000, "height": 1000}],
            status=Paper.Status.READY,
        )
        pages = paper_dir(self.paper) / "pages"
        pages.mkdir(parents=True)
        for index in range(2):
            ruled_page(500).save(pages / f"page_{index}.png")
        self.top_key = "0:100,800,900,990"
        self.bottom_key = "1:100,10,900,60"
        self.question = Question.objects.create(
            paper=self.paper, number=19, question_type="free_response",
            regions=[{"page_idx": 0, "bbox": [50, 500, 950, 995]}, {"page_idx": 1, "bbox": [50, 5, 950, 400]}],
            regions_auto=[{"page_idx": 0, "bbox": [50, 500, 950, 995]}, {"page_idx": 1, "bbox": [50, 5, 950, 400]}],
            figure_candidates=[{"page_idx": 0, "bbox": TOP}, {"page_idx": 1, "bbox": BOTTOM}],
            stem="（1）填写如下列联表：", state=Question.State.GREEN,
        )

    def post(self, action: str, body: dict):
        return self.client.post(
            f"/api/questions/{self.question.pk}/{action}",
            data=json.dumps(body), content_type="application/json", HTTP_X_QB_REQUEST="1",
        )

    def save_joined(self):
        return self.post("figures", {"figures": [{
            "page_idx": 0, "bbox": TOP, "slot": "stem", "candidate_key": self.top_key,
            "parts": [{"page_idx": 1, "bbox": BOTTOM, "candidate_key": self.bottom_key}],
        }], "ignored_candidates": []})

    def test_joined_figure_is_saved_served_and_published_as_one_image(self):
        response = self.save_joined()
        self.assertEqual(response.status_code, 200, response.content)
        figure = response.json()["question"]["figures"][0]
        self.assertEqual(figure["parts"], [{"page_idx": 1, "bbox": BOTTOM, "candidate_key": self.bottom_key}])
        # Both candidates are used, so nothing is left unclassified.
        self.assertEqual(response.json()["question"]["figure_review"]["status"], "ok")

        served = self.client.get(figure["url"])
        image = Image.open(io.BytesIO(b"".join(served.streaming_content)))
        served.close()  # Windows keeps an open file from being removed
        self.assertEqual(image.size, (800, 190 + 50))

        question = Question.objects.select_related("paper", "group").get(pk=self.question.pk)
        content = library.final_content(question)
        self.assertEqual(content["figures"][0]["parts"], [{"page_idx": 1, "bbox": BOTTOM}])
        question.approved = True
        question.approved_at = timezone.now()
        question.approved_content_hash = library.approval_hash(question)
        question.save()
        publication, created = library.publish(question)
        self.assertTrue(created)
        stored = paper_dir(self.paper).parent / "library"
        files = [path for path in Path(stored).rglob("figure-1.png")]
        self.assertEqual(len(files), 1)
        self.assertEqual(Image.open(files[0]).size, (800, 240))
        self.assertEqual(PublishedQuestion.objects.count(), 1)

    def test_unjoining_changes_the_image_and_the_approval_hash(self):
        joined_url = self.save_joined().json()["question"]["figures"][0]["url"]
        joined = Question.objects.select_related("paper", "group").get(pk=self.question.pk)
        joined_hash = library.approval_hash(joined)
        response = self.post("figures", {"figures": [
            {"page_idx": 0, "bbox": TOP, "slot": "stem", "candidate_key": self.top_key},
            {"page_idx": 1, "bbox": BOTTOM, "slot": "stem", "candidate_key": self.bottom_key},
        ], "ignored_candidates": []})
        self.assertEqual(response.status_code, 200, response.content)
        split = Question.objects.select_related("paper", "group").get(pk=self.question.pk)
        self.assertNotEqual(library.approval_hash(split), joined_hash)
        urls = [figure["url"] for figure in response.json()["question"]["figures"]]
        self.assertEqual(len(urls), 2)
        self.assertNotIn(joined_url, urls)
        # Same first piece, different image: the version in the URL must change.
        self.assertNotEqual(urls[0].split("?v=")[1], joined_url.split("?v=")[1])
        self.assertNotIn("parts", split.figures[0])

    def test_a_single_piece_figure_keeps_its_hash_and_url(self):
        figure = {"slot": "stem", "page_idx": 0, "bbox": TOP, "source": "manual"}
        self.assertEqual(library.figure_identity(figure), json.dumps([0, TOP]))
        content = {"figures": [figure]}
        legacy = {"figures": [{**figure, "parts": []}]}
        self.assertEqual(library.content_hash(content), library.content_hash(legacy))

    def test_bad_parts_are_refused(self):
        for parts in ([{"page_idx": 9, "bbox": BOTTOM}], [{"page_idx": 1, "bbox": [1, 1, 1, 1]}],
                      [{"page_idx": 1, "bbox": BOTTOM}] * (library.MAX_FIGURE_PARTS + 1), "x",
                      [{"page_idx": 1, "bbox": BOTTOM, "candidate_key": "1:0,0,5,5"}]):
            with self.subTest(parts=parts):
                response = self.post("figures", {"figures": [
                    {"page_idx": 0, "bbox": TOP, "slot": "stem", "parts": parts},
                ], "ignored_candidates": []})
                self.assertEqual(response.status_code, 400)

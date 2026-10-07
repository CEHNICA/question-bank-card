"""Local cut-strip contrast checks with explicit, synthetic ink geometry.

These fixtures validate crop invariants, not accuracy on real phone photos.
"""
from __future__ import annotations

from django.test import SimpleTestCase
from PIL import Image, ImageDraw

from . import cuts
from .test_v1101_cuts import COLUMN, item, page


def recolor(image, *, paper, ink):
    return image.convert("L").point(lambda value: ink if value < cuts.INK_LEVEL else paper)


class CutContrastTests(SimpleTestCase):
    def test_dark_paper_and_pale_ink_keep_the_same_known_row_profile(self):
        reference = page(lines=[(492, 498)], strokes=[(300, 500, 303, 506)])
        expected = cuts.row_ink(reference, *COLUMN, 480, 520)
        for paper, ink in ((145, 85), (245, 185)):
            with self.subTest(paper=paper, ink=ink):
                photo = recolor(reference, paper=paper, ink=ink)
                before = photo.tobytes()
                self.assertEqual(cuts.row_ink(photo, *COLUMN, 480, 520), expected)
                self.assertEqual(photo.tobytes(), before)

    def test_reliable_contrast_repairs_a_fraction_without_losing_the_next_first_line(self):
        reference = page(lines=[(800, 830), (866, 890)],
                         strokes=[(100, 836, 108, 856), (220, 836, 228, 856), (330, 836, 338, 856)])
        for paper, ink in ((145, 85), (245, 185)):
            with self.subTest(paper=paper, ink=ink):
                image = recolor(reference, paper=paper, ink=ink)
                q6 = item(6, [[COLUMN[0], 765, COLUMN[1], 852]], y=774)
                q7 = item(7, [[COLUMN[0], 845, COLUMN[1], 972]], y=854)
                self.assertEqual(cuts.snap_cuts([q6, q7], lambda _p: image), 1)
                self.assertGreater(q6["regions"][0]["bbox"][3], 856)
                self.assertGreater(q7["regions"][0]["bbox"][1], 856)
                self.assertLess(q7["regions"][0]["bbox"][1], 866)

    def test_pale_and_dark_photos_do_not_give_away_a_thin_numerator(self):
        reference = page(lines=[(480, 497), (507, 519)], strokes=[(300, 500, 303, 506)])
        for paper, ink in ((145, 85), (245, 185)):
            with self.subTest(paper=paper, ink=ink):
                image = recolor(reference, paper=paper, ink=ink)
                q1 = item(1, [[COLUMN[0], 300, COLUMN[1], 498]], y=310)
                q2 = item(2, [[COLUMN[0], 491, COLUMN[1], 700]], y=500)
                cuts.snap_cuts([q1, q2], lambda _p: image)
                self.assertLessEqual(q2["regions"][0]["bbox"][1], 500)

    def test_dark_uniform_strip_has_no_evidence_to_invent_a_white_gap(self):
        image = Image.new("L", (500, 1000), 145)
        rows = cuts.row_ink(image, *COLUMN, 480, 520)
        self.assertTrue(all(share == 1 for _y, share in rows))
        self.assertIsNone(cuts.first_gap(rows, 490, 515))

    def test_lighter_strokes_up_to_the_paper_mode_are_not_erased(self):
        for shade in (138, 143, 144):
            with self.subTest(shade=shade):
                image = Image.new("L", (1000, 1000), 145)
                draw = ImageDraw.Draw(image)
                draw.rectangle((100, 490, 300, 494), fill=85)
                draw.rectangle((100, 500, 400, 504), fill=shade)
                rows = cuts.row_ink(image, 0, 500, 480, 520)
                self.assertTrue(all(share > cuts.DENSE for y, share in rows if 500 <= y <= 504))

    def test_nearly_overlapping_gray_groups_keep_the_existing_cut(self):
        reference = page(lines=[(489, 496), (500, 506), (515, 530)])
        image = recolor(reference, paper=145, ink=135)
        q1 = item(1, [[COLUMN[0], 300, COLUMN[1], 498]], y=310)
        q2 = item(2, [[COLUMN[0], 491, COLUMN[1], 700]], y=500)
        self.assertEqual(cuts.snap_cuts([q1, q2], lambda _p: image), 0)
        self.assertEqual(q1["regions"][0]["bbox"][3], 498)
        self.assertEqual(q2["regions"][0]["bbox"][1], 491)

    def test_two_large_side_by_side_shadow_zones_cannot_be_called_a_gap(self):
        image = Image.new("L", (1000, 1000), 245)
        ImageDraw.Draw(image).rectangle((0, 0, 499, 999), fill=145)
        rows = cuts.row_ink(image, 0, 1000, 480, 520)
        self.assertTrue(all(share >= 0.49 for _y, share in rows))
        self.assertIsNone(cuts.first_gap(rows, 490, 515))

    def test_each_column_uses_its_own_local_paper_brightness(self):
        image = Image.new("L", (1000, 1000), 245)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 499, 999), fill=145)
        draw.rectangle((100, 492, 300, 498), fill=85)
        draw.rectangle((600, 492, 800, 498), fill=185)
        left = cuts.row_ink(image, 0, 500, 480, 520)
        right = cuts.row_ink(image, 500, 1000, 480, 520)
        self.assertEqual(left, right)
        self.assertTrue(any(share > cuts.DENSE for _y, share in left))
        self.assertTrue(any(share == 0 for _y, share in left))

    def test_clean_black_and_white_row_profiles_stay_unchanged(self):
        reference = page(lines=[(480, 497), (507, 519)], strokes=[(300, 500, 303, 506)])
        gray = reference.crop((24, 970, 738, 1040)).convert("L")
        legacy = gray.point(lambda value: 255 if value < cuts.INK_LEVEL else 0)
        means = legacy.resize((1, legacy.height), Image.Resampling.BOX)
        expected = [((970 + row + 0.5) * 1000 / reference.height, means.getpixel((0, row)) / 255)
                    for row in range(means.height)]
        self.assertEqual(cuts.row_ink(reference, *COLUMN, 485, 520), expected)

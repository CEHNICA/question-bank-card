"""End-to-end Word export with a real browser rendering one formula to a picture.

Unit tests mock the renderer, which is exactly where a broken CDP call, a
mis-measured box or a blank screenshot would hide. This module runs the real
headless browser against a formula Word cannot express. It is opt-in because it
starts a process and takes seconds:

    set QB_EXPORT_BROWSER_TEST=1 && backend\\.venv\\Scripts\\python.exe backend\\manage.py test core.test_library_export_browser

Nothing here may be mocked; a skip must say why, never print ok.
"""

from __future__ import annotations

import io
import json
import os
import unittest
import zipfile

from django.test import TestCase, override_settings
from lxml import etree
from PIL import Image

from . import library_export as export
from .test_library_export import ExportFixture, math_field, units

NS = {"m": export.OMML_NS, "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


@unittest.skipUnless(os.environ.get("QB_EXPORT_BROWSER_TEST") == "1",
                     "set QB_EXPORT_BROWSER_TEST=1 to run the real browser export")
@override_settings(ROOT_URLCONF="core.test_library_export")
class WordFormulaPictureBrowserTests(ExportFixture, TestCase):
    """Reuses the publication/payload/post fixtures; only the browser is real."""

    def degraded(self, latex, head="化简 ", tail=" 并写出结果。"):
        # Real KaTeX shapes for the two enclosures Word's equation model drops.
        command, _, rest = latex[1:].partition("{")
        notation = {"cancel": "updiagonalstrike", "bcancel": "downdiagonalstrike",
                    "xcancel": "updiagonalstrike downdiagonalstrike"}[command]
        variable = rest[:-1]
        body = f'<menclose notation="{notation}"><mi>{variable}</mi></menclose>'
        source = head + "$" + latex + "$" + tail
        mathml = (f'<math xmlns="{export.MATH_NS}"><semantics>{body}'
                  f'<annotation encoding="application/x-tex">{latex}</annotation></semantics></math>')
        head_units = units(head)
        cut = head_units + units("$" + latex + "$")
        return {"source": source, "blocks": [{"type": "text", "start": 0, "end": units(source), "segments": [
            {"type": "text", "start": 0, "end": head_units},
            {"type": "math", "start": head_units, "end": cut, "latex": latex, "mathml": mathml, "display": False},
            {"type": "text", "start": cut, "end": units(source)}]}]}

    def test_one_real_browser_renders_two_formulas_into_the_docx(self):
        first = self.publication(stem=self.degraded("\\cancel{x}")["source"])
        second = self.publication(stem=self.degraded("\\bcancel{y}")["source"])
        payload = self.payload([first, second])
        payload["rendered_fields"][str(first.id)]["stem"] = self.degraded("\\cancel{x}")
        payload["rendered_fields"][str(second.id)]["stem"] = self.degraded("\\bcancel{y}")
        result = self.post(payload)
        self.assertEqual(result.status_code, 200, result.content[:400])

        xml = etree.fromstring(zipfile.ZipFile(io.BytesIO(result.content)).read("word/document.xml"))
        self.assertEqual(len(xml.findall(".//a:blip", NS)), 2)
        text = "".join(xml.itertext())
        self.assertNotIn("\\cancel", text)
        self.assertNotIn("\\bcancel", text)
        self.assertEqual(text.count("化简"), 2)

        # Each picture must actually carry its formula: not blank, not a rectangle
        # of the app's paper colour, and shaped by what it renders.
        with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
            media = sorted(name for name in archive.namelist() if name.startswith("word/media/"))
            self.assertEqual(len(media), 2)
            bodies = []
            for name in media:
                with Image.open(io.BytesIO(archive.read(name))) as image:
                    image.load()
                    white = Image.new("RGBA", image.size, (255, 255, 255, 255))
                    flat = Image.alpha_composite(white, image.convert("RGBA")).convert("RGB")
                    colours = flat.getcolors(maxcolors=4)
                    self.assertTrue(colours is None or len(colours) > 1,
                                    f"{name} 是一张空图，卷子上会留个洞")
                    # Glyphs have real extent; a one-column strip is not a formula.
                    self.assertGreaterEqual(min(image.size), 24, f"{name} 的尺寸不像一条公式")
                    # No backdrop: the app stylesheet paints the paper colour on
                    # html/body, and that would print a beige box behind the formula.
                    alpha = image.convert("RGBA").getextrema()[3]
                    self.assertEqual(alpha[0], 0, f"{name} 带了底色，Word 里会是个方块")
                    bodies.append(archive.read(name))
            self.assertNotEqual(bodies[0], bodies[1], "两条不同的公式截出了同一张图")

        from urllib.parse import unquote
        warning = unquote(result["X-QB-Layout-Warning"])
        self.assertIn("第 1 题", warning)
        self.assertIn("第 2 题", warning)
        self.assertIn("题干", warning)

    def test_a_convertible_formula_still_comes_out_as_a_native_equation(self):
        """The picture path must not swallow formulas that never needed it."""
        value = math_field(r"\frac{a}{b}+\sqrt{x}", '<mrow><mfrac><mi>a</mi><mi>b</mi></mfrac><mo>+</mo>'
                           '<msqrt><mi>x</mi></msqrt></mrow>', display=True)
        pub = self.publication(stem=value["source"])
        payload = self.payload([pub])
        payload["rendered_fields"][str(pub.id)]["stem"] = value
        result = self.post(payload)
        self.assertEqual(result.status_code, 200, result.content[:400])
        xml = etree.fromstring(zipfile.ZipFile(io.BytesIO(result.content)).read("word/document.xml"))
        self.assertEqual(len(xml.findall(".//m:oMath", NS)), 1)
        self.assertTrue(xml.findall(".//m:f", NS))
        self.assertEqual(len(xml.findall(".//a:blip", NS)), 0)
        self.assertIsNone(result.get("X-QB-Layout-Warning"))
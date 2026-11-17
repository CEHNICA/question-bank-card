"""The exported PDF must carry its own fonts, or printing it elsewhere breaks.

A PDF that references a font it does not embed looks fine on the machine that
made it and turns into blanks, tofu boxes or a substituted typeface on the
teacher's other computer or in the school's print shop.  Nothing in the app
can tell the teacher that, because the export is otherwise successful.

So this module renders a real PDF in a real browser (the only place where the
fonts are actually chosen) and checks every font in the result carries its own
program.  It is opt-in because it starts a browser and takes seconds:

    set QB_EXPORT_BROWSER_TEST=1 && backend\\manage.py test core.test_library_pdf_fonts

Two rules apply to a check like this one:

* A skip must say why.  Never print ok for a paper that was never opened.
* The check itself must be proven able to fail.  ``test_the_checker_flags_a_font
  _that_is_not_embedded`` builds a PDF with a plain Helvetica reference and
  requires the same helper to report it, so a helper that always returns "fine"
  cannot pass this module.
"""

from __future__ import annotations

import os
import re
import unittest

from django.test import TestCase

from . import library_pdf as pdf
from .test_library_export import ExportFixture, units

# "12 0 R" — an indirect reference inside a PDF object.
REFERENCE = re.compile(r"(\d+)\s+\d+\s+R")


def _references(value: str) -> list[int]:
    return [int(match.group(1)) for match in REFERENCE.finditer(value or "")]


def _font_program_missing(document, xref: int, subtype: str) -> bool:
    """True when this font has no glyph program of its own.

    Three shapes show up in Chrome's output:

    * ``/Type3`` — the glyphs are drawing procedures in the page content.
      Self-contained by construction.
    * ``/Type0`` with a descendant ``/CIDFontType2`` — the program hangs off
      the descendant's ``/FontDescriptor``, one level down.
    * a simple ``/Type1`` or ``/TrueType`` — the program hangs off the font's
      own ``/FontDescriptor``.
    """
    if subtype == "Type3":
        return False
    descriptors: list[int] = []
    kind, value = document.xref_get_key(xref, "FontDescriptor")
    if kind in {"xref", "indirect"}:
        descriptors.extend(_references(value))
    kind, value = document.xref_get_key(xref, "DescendantFonts")
    if kind == "array":
        for descendant in _references(value):
            kind, value = document.xref_get_key(descendant, "FontDescriptor")
            if kind in {"xref", "indirect"}:
                descriptors.extend(_references(value))
    if not descriptors:
        # No descriptor at all means no embedded program either.
        return True
    for descriptor in descriptors:
        for key in ("FontFile", "FontFile2", "FontFile3"):
            if document.xref_get_key(descriptor, key)[0] != "null":
                return False
    return True


def unembedded_fonts(data: bytes) -> list[str]:
    """Every font in the PDF that does not carry its own program."""
    import pymupdf

    missing: list[str] = []
    with pymupdf.open(stream=data, filetype="pdf") as document:
        for page in document:
            for font in page.get_fonts(full=True):
                xref, _extension, _type, basefont, _name, _encoding, _referencer = font
                kind, value = document.xref_get_key(xref, "Subtype")
                subtype = value.lstrip("/") if kind == "name" else ""
                if _font_program_missing(document, xref, subtype):
                    missing.append(basefont or f"font-{xref}")
    return missing


class FontCheckerTests(TestCase):
    """The checker itself, with no browser involved.

    A check that cannot fail protects nothing, so it is proved here on a PDF
    that deliberately references a font without embedding it.
    """

    def test_the_checker_flags_a_font_that_is_not_embedded(self):
        import pymupdf

        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((72, 100), "Helvetica is not embedded here", fontname="helv", fontsize=14)
        data = document.tobytes()
        document.close()
        self.assertEqual(unembedded_fonts(data), ["Helvetica"])

    def test_the_checker_calls_a_standalone_font_missing_its_program(self):
        """A simple Type1 font has no descriptor at all — still not embedded."""
        import pymupdf

        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((72, 100), "Another unembedded reference", fontname="tiro", fontsize=14)
        data = document.tobytes()
        document.close()
        self.assertEqual(unembedded_fonts(data), ["Times-Roman"])


@unittest.skipUnless(
    os.environ.get("QB_EXPORT_BROWSER_TEST") == "1",
    "set QB_EXPORT_BROWSER_TEST=1 to render the real PDF and check its fonts",
)
class PdfFontEmbeddingTests(ExportFixture, TestCase):
    """Renders one real PDF and requires every font in it to be embedded."""

    LATEX = "x^2+\\frac{1}{2}"
    MATHML = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><semantics>'
        "<mrow><msup><mi>x</mi><mn>2</mn></msup><mo>+</mo>"
        "<mfrac><mn>1</mn><mn>2</mn></mfrac></mrow>"
        '<annotation encoding="application/x-tex">x^2+\\frac{1}{2}</annotation>'
        "</semantics></math>"
    )

    def stem_with_formula(self, head, tail):
        """The same shape the preview sends: text, one formula, text.

        The export refuses a formula that is still LaTeX source inside a plain
        text segment, so the formula has to be its own segment — exactly like
        the browser sends it.
        """
        source = head + "$" + self.LATEX + "$" + tail
        head_units = units(head)
        cut = head_units + units("$" + self.LATEX + "$")
        return source, {
            "source": source,
            "blocks": [
                {
                    "type": "text",
                    "start": 0,
                    "end": units(source),
                    "segments": [
                        {"type": "text", "start": 0, "end": head_units},
                        {
                            "type": "math",
                            "start": head_units,
                            "end": cut,
                            "latex": self.LATEX,
                            "mathml": self.MATHML,
                            "display": False,
                        },
                        {"type": "text", "start": cut, "end": units(source)},
                    ],
                }
            ],
        }

    def test_every_font_in_the_exported_pdf_carries_its_own_program(self):
        head, tail = "中文题干：求 ", " 的值。"
        source, stem_field = self.stem_with_formula(head, tail)
        pub = self.publication(stem=source, answer="答案一", analysis="解析：先配方。", origin="合成出处")
        payload = self.payload([pub], output_format="pdf", print_options={"document": "combined"})
        payload["rendered_fields"][str(pub.id)]["stem"] = stem_field
        data, filename, count, pages = pdf.export(payload)
        self.assertEqual(count, 1)
        self.assertGreaterEqual(pages, 1)
        self.assertTrue(filename.endswith(".pdf"))

        self.assertEqual(unembedded_fonts(data), [], "导出的 PDF 里有没内嵌的字体，换台电脑印会变成方块")

        # The check above is only worth something if the paper really used
        # fonts.  A PDF with no fonts at all would pass it trivially.
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as document:
            names = [font[3] for page in document for font in page.get_fonts(full=True)]
        self.assertTrue(names, "导出的 PDF 里一个字体都没有，这条守护等于没测")
        self.assertTrue(
            any("KaTeX" in name for name in names), f"公式没有用 KaTeX 字体渲染，公式字体这条没被测到：{names}"
        )
        self.assertTrue(
            any("STSong" in name or "YaHei" in name or "SimSun" in name for name in names),
            f"中文没有用中文字体渲染，中文字体这条没被测到：{names}",
        )

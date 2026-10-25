"""Build a small docx with the real figure writers and let Word measure it.

The unit tests can only see the XML they asked for. This produces an actual
document through library_export's own code path, so Word itself can be asked
where every picture lands. Run tools/measure_word_pictures.ps1 on the output.
"""
import io
import os
import sys
import tempfile

sys.path.insert(0, r"C:\Users\17321\.minimax\sessions\mvs_fc946281665f4565a3d0ce7af01bd838\workspace\qbj-src\backend")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")
import django

django.setup()

from docx import Document
from PIL import Image

from core.library_export import _figure_row_lines, _write_figure_line, _write_images


def picture(size, colour="red"):
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return {"bytes": buffer.getvalue(), "size": size, "slot": "stem"}


def build(path):
    document = Document()
    # The app's A4 text area: 16 mm margins, 178 mm between them.
    for section in document.sections:
        section.left_margin = section.right_margin = 16 * 36000

    # The reported case: three diagrams that belong on one row.
    grid = [picture((279, 226)), picture((231, 226)), picture((210, 236))]
    lines = _figure_row_lines(grid)
    _write_figure_line(document, grid)

    # The other reported case: a set that wraps 2+1 (必修第一册第 4 题's real
    # widths), where the lone second line used to land hard against the left of
    # an otherwise centred table.
    wrapped = [picture((415, 231)), picture((419, 228)), picture((419, 228))]
    wrap_lines = _figure_row_lines(wrapped)
    _write_figure_line(document, wrapped[:wrap_lines[0]])
    _write_figure_line(document, wrapped[wrap_lines[0]:])

    # A lone stem figure: the case that was hugging the left margin.
    _write_images(document, {"images": [picture((294, 201))]}, "stem")

    # A scanned page stays left, because the preview leaves it left.
    body = [dict(picture((400, 560)), slot="body"), dict(picture((380, 540)), slot="body")]
    _write_images(document, {"images": body}, "body")

    document.save(path)
    return lines


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        tempfile.gettempdir(), "qb-figure-centring.docx")
    columns = build(target)
    print(f"wrote {target} (first figure row splits {columns})")

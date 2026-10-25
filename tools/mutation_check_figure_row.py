"""Mutation check: the figure-layout tests must fail when the rules are undone.

Each mutation reverts one part of the behaviour and requires the named tests
to go red. A mutation that leaves them green proves the test does not check
that part at all.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"C:\Users\17321\.minimax\sessions\mvs_fc946281665f4565a3d0ce7af01bd838\workspace\qbj-src")
TARGET = ROOT / "backend" / "core" / "library_export.py"
TESTS = "core.test_library_export.LibraryExportTests"

MUTATIONS = [
    (
        "figures go back to a 96 dpi guess instead of the preview's 150 dpi",
        "PREVIEW_FIGURE_DPI = 150",
        "PREVIEW_FIGURE_DPI = 96",
    ),
    (
        "a width typed in the preview is read as centimetres, so 60 lands as 6",
        '    overrides = (settings or {}).get("width") or {}',
        '    overrides = {key: value / 10 for key, value in ((settings or {}).get("width") or {}).items()}',
    ),
    ("a figure may grow wider than the text area", "PREVIEW_TEXT_WIDTH_MM = 178", "PREVIEW_TEXT_WIDTH_MM = 1000"),
    ("a figure may grow taller than one page", "PREVIEW_PAGE_HEIGHT_MM = 210", "PREVIEW_PAGE_HEIGHT_MM = 2100"),
    (
        "the row stops wrapping and takes every figure",
        "        if current and used + gap + width > text_width_mm + 0.01:",
        "        if False:",
    ),
    (
        "the line lengths collapse back to one uniform count, losing the lone tail",
        "    if current:\n        lines.append(current)\n    return lines or [1]",
        "    if current:\n        lines.append(current)\n    return [len(widths)] or [1]",
    ),
    (
        "the gap between figures is guessed at 1/72 inch instead of read off the row",
        "    gap = PREVIEW_FIGURE_GAP_PX * 25.4 / PREVIEW_PX_PER_INCH\n    forced = set(forced or ())",
        "    gap = PREVIEW_FIGURE_GAP_PX * 25.4 / 72\n    forced = set(forced or ())",
    ),
    (
        "every line is packed into a single table again, so a lone tail sits left",
        "    if lines and max(lines) > 1:",
        "    if lines and sum(lines) > 1:",
    ),
    (
        "the paragraph that keeps two figure lines apart is dropped, Word merges them",
        "            _figure_row_separator(document)",
        "            pass",
    ),
    (
        "a row with no position chosen falls back to the left margin instead of centred",
        '    table.alignment = {"left": WD_TABLE_ALIGNMENT.LEFT, "right": WD_TABLE_ALIGNMENT.RIGHT}.get(\n'
        "        align, WD_TABLE_ALIGNMENT.CENTER)",
        '    table.alignment = {"left": WD_TABLE_ALIGNMENT.LEFT, "right": WD_TABLE_ALIGNMENT.RIGHT}.get(\n'
        "        align, WD_TABLE_ALIGNMENT.LEFT)",
    ),
    (
        "the grid cell stops centring the picture inside its column",
        '        paragraph.alignment = {"left": 0, "right": 2}.get(align, 1)',
        "        paragraph.alignment = 0",
    ),
    (
        "columns stop carrying the gap between figures",
        "        column_widths = [width + gap_mm for width in widths]",
        "        column_widths = [width for width in widths]",
    ),
    (
        "the last column loses its gap again and the row drifts right",
        "        column_widths = [width + gap_mm for width in widths]",
        "        column_widths = [width + gap_mm for width in widths[:-1]] + widths[-1:]",
    ),
    (
        "a tight row is shrunk instead of letting the empty ends spill",
        "    row_mm = sum(widths) + gap_mm * max(len(widths) - 1, 0)",
        "    row_mm = sum(widths) + gap_mm * max(len(widths), 0)",
    ),
    (
        "a lone stem figure goes back to the left margin",
        '            paragraph.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT,\n'
        '                                    "right": WD_ALIGN_PARAGRAPH.RIGHT}.get(align, WD_ALIGN_PARAGRAPH.CENTER)',
        "            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT",
    ),
    (
        "a scanned page gets centred too, which the preview does not do",
        '        if slot != "body":\n'
        "            from docx.enum.text import WD_ALIGN_PARAGRAPH\n"
        '            paragraph.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT,',
        "        from docx.enum.text import WD_ALIGN_PARAGRAPH\n"
        "        if True:\n"
        '            paragraph.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT,',
    ),
    (
        "the name／class／score line goes back against the left margin",
        "        info.alignment = WD_ALIGN_PARAGRAPH.CENTER",
        "        info.alignment = WD_ALIGN_PARAGRAPH.LEFT",
    ),
    (
        "the name／class／score line loses its alignment entirely",
        "        info.alignment = WD_ALIGN_PARAGRAPH.CENTER\n        info.paragraph_format.keep_with_next = True",
        "        info.paragraph_format.keep_with_next = True",
    ),
    (
        "Word's default cell padding comes back and drags the row off centre",
        '        edge.set(qn("w:w"), "0")',
        '        edge.set(qn("w:w"), "108")',
    ),
    (
        "the table width is left for Word to re-infer",
        '    table_width.set(qn("w:type"), "dxa")',
        '    table_width.set(qn("w:type"), "auto")',
    ),
    (
        "table properties are appended instead of inserted in order",
        "    properties.insert_element_before(\n"
        '        borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook",\n'
        '        "w:tblCaption", "w:tblDescription")',
        "    properties.append(borders)",
    ),
    # --- 老师自己调的排版（1.24.8 起）------------------------------------------
    (
        "a width typed in the preview is ignored and every figure reverts to 150 dpi",
        '    return [_figure_paper_size(image["size"], text_width_mm, forced_mm=overrides.get(index))[0]',
        '    return [_figure_paper_size(image["size"], text_width_mm)[0]',
    ),
    (
        "a width typed in the preview scales the 150 dpi size instead of replacing it",
        "    paper = min(float(forced_mm) if forced_mm else width * 25.4 / PREVIEW_FIGURE_DPI,",
        "    paper = min(float(forced_mm or 1) * width * 25.4 / PREVIEW_FIGURE_DPI / 100 if forced_mm else width * 25.4 / PREVIEW_FIGURE_DPI,",
    ),
    (
        "a widened diagram is no longer capped at the text area",
        "    paper = min(float(forced_mm) if forced_mm else width * 25.4 / PREVIEW_FIGURE_DPI,\n"
        "                float(text_width_mm),",
        "    paper = min(float(forced_mm) if forced_mm else width * 25.4 / PREVIEW_FIGURE_DPI,\n"
        "                float(text_width_mm) * 10,",
    ),
    (
        "a diagram the teacher widened is allowed past one page of height",
        "                page_height_mm * width / height)",
        "                page_height_mm * 10 * width / height)",
    ),
    (
        "a figure stretched with its width instead of keeping its own proportions",
        '    heights = [width * float(image["size"][1]) / float(image["size"][0])\n'
        "               for width, image in zip(widths, pictures)]",
        '    heights = [float(image["size"][1]) * 25.4 / PREVIEW_FIGURE_DPI\n               for image in pictures]',
    ),
    (
        "the row ignores the line the teacher asked for and packs greedily again",
        "        if index in forced and current:\n"
        "            lines.append(current)\n"
        "            current, used = 0, 0.0",
        "        if False:\n            lines.append(current)\n            current, used = 0, 0.0",
    ),
    (
        "a left-aligned row keeps the centred row's trailing gap and sits 1.85mm off",
        '    if align == "left":\n'
        "        column_widths = [width + (gap_mm if position < len(widths) - 1 else 0.0)\n"
        "                         for position, width in enumerate(widths)]",
        '    if align == "never":\n        column_widths = [width + gap_mm for width in widths]',
    ),
    (
        "a right-aligned row keeps the centred row's leading gap",
        '    elif align == "right":\n'
        "        column_widths = [width + (gap_mm if position else 0.0)\n"
        "                         for position, width in enumerate(widths)]",
        '    elif align == "never":\n        column_widths = [width + gap_mm for width in widths]',
    ),
    (
        "the table stops following the position the teacher chose",
        '    table.alignment = {"left": WD_TABLE_ALIGNMENT.LEFT, "right": WD_TABLE_ALIGNMENT.RIGHT}.get(\n'
        "        align, WD_TABLE_ALIGNMENT.CENTER)",
        "    table.alignment = WD_TABLE_ALIGNMENT.CENTER",
    ),
    (
        "one question's figure settings are applied to every question on the paper",
        '    settings = (options.get("figure_overrides") or {}).get(item["id"]) or {}',
        '    settings = next(iter((options.get("figure_overrides") or {}).values()), None) or {}',
    ),
    (
        "a lone figure ignores the position the teacher chose",
        '            paragraph.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT,\n'
        '                                    "right": WD_ALIGN_PARAGRAPH.RIGHT}.get(align, WD_ALIGN_PARAGRAPH.CENTER)',
        "            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER",
    ),
]

original = TARGET.read_text(encoding="utf-8")
backup = Path(tempfile.mkdtemp()) / "library_export.py"
shutil.copy2(TARGET, backup)


def write(text):
    # newline="" keeps the file's own line endings. Without it Windows turns every
    # \n into \r\n, and the "restored byte for byte" check at the end would be
    # checking the wrong thing — read_text normalises both sides away.
    TARGET.write_text(text, encoding="utf-8", newline="")


failed = 0
try:
    for label, old, new in MUTATIONS:
        if original.count(old) != 1:
            print(f"[BAD ] {label}: pattern found {original.count(old)} times, need exactly 1")
            failed += 1
            continue
        write(original.replace(old, new))
        result = subprocess.run(
            [str(ROOT / "packaging" / ".build" / "venv" / "Scripts" / "python.exe"), "manage.py", "test", TESTS],
            cwd=ROOT / "backend",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out = result.stdout + result.stderr
        red = result.returncode != 0
        names = sorted(
            {line.split("]")[-1].strip().split("\n")[0] for line in out.splitlines() if line.startswith("FAIL:")}
        )
        if red:
            print(f"[PASS] {label}")
            for name in names:
                print(f"         caught by: {name}")
        else:
            print(f"[FAIL] {label}: tests still passed, so nothing checks this")
            failed += 1
finally:
    write(original)
    if TARGET.read_bytes() != Path(backup).read_bytes():
        print("[BAD ] source was not restored byte for byte")
        failed += 1

print()
print("all mutations caught" if failed == 0 else f"{failed} problem(s)")
sys.exit(1 if failed else 0)

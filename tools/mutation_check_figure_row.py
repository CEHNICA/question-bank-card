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
        "figures are sized against the paper font size again, not the preview's dpi",
        "    paper = min(width * 25.4 / PREVIEW_FIGURE_DPI,",
        "    paper = min(width * 25.4 / 72 / 17,",
    ),
    (
        "a figure may grow wider than the text area",
        "PREVIEW_TEXT_WIDTH_MM = 178",
        "PREVIEW_TEXT_WIDTH_MM = 1000",
    ),
    (
        "a figure may grow taller than one page",
        "PREVIEW_PAGE_HEIGHT_MM = 210",
        "PREVIEW_PAGE_HEIGHT_MM = 2100",
    ),
    (
        "the row stops wrapping and takes every figure",
        "        if used + gap + width <= text_width_mm + 0.01:",
        "        if True:",
    ),
    (
        "the line lengths collapse back to one uniform count, losing the lone tail",
        "    lines.append(current)\n    return lines",
        "    lines.append(current)\n    return [len(widths)]",
    ),
    (
        "the gap between figures is guessed at 1/72 inch instead of read off the row",
        "    gap = PREVIEW_FIGURE_GAP_PX * 25.4 / PREVIEW_PX_PER_INCH",
        "    gap = PREVIEW_FIGURE_GAP_PX * 25.4 / 72",
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
        "the figure row stops being centred",
        "    table.autofit = False\n"
        "    table.alignment = WD_TABLE_ALIGNMENT.CENTER\n"
        "    for column, width in zip(table.columns, column_widths):",
        "    table.autofit = False\n"
        "    table.alignment = WD_TABLE_ALIGNMENT.LEFT\n"
        "    for column, width in zip(table.columns, column_widths):",
    ),
    (
        "the grid cell stops centring the picture inside its column",
        "        # .qb-figures centres its row; the grid cells do the same job here.\n"
        "        paragraph.alignment = 1",
        "        # .qb-figures centres its row; the grid cells do the same job here.\n"
        "        paragraph.alignment = 0",
    ),
    (
        "columns stop carrying the gap between figures",
        "    column_widths = [width + gap_mm for width in widths]",
        "    column_widths = [width for width in widths]",
    ),
    (
        "the last column loses its gap again and the row drifts right",
        "    column_widths = [width + gap_mm for width in widths]",
        "    column_widths = [width + gap_mm for width in widths[:-1]] + widths[-1:]",
    ),
    (
        "a tight row is shrunk instead of letting the empty ends spill",
        "    row_mm = sum(widths) + gap_mm * max(len(widths) - 1, 0)",
        "    row_mm = sum(widths) + gap_mm * max(len(widths), 0)",
    ),
    (
        "a lone stem figure goes back to the left margin",
        "            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER",
        "            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT",
    ),
    (
        "a scanned page gets centred too, which the preview does not do",
        "        if slot != \"body\":\n"
        "            from docx.enum.text import WD_ALIGN_PARAGRAPH\n"
        "            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER",
        "        from docx.enum.text import WD_ALIGN_PARAGRAPH\n"
        "        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER",
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
        '    properties.insert_element_before(\n'
        '        borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook",\n'
        '        "w:tblCaption", "w:tblDescription")',
        "    properties.append(borders)",
    ),
]

original = TARGET.read_text(encoding="utf-8")
backup = Path(tempfile.mkdtemp()) / "library_export.py"
shutil.copy2(TARGET, backup)

failed = 0
try:
    for label, old, new in MUTATIONS:
        if original.count(old) != 1:
            print(f"[BAD ] {label}: pattern found {original.count(old)} times, need exactly 1")
            failed += 1
            continue
        TARGET.write_text(original.replace(old, new), encoding="utf-8")
        result = subprocess.run(
            [str(ROOT / "packaging" / ".build" / "venv" / "Scripts" / "python.exe"),
             "manage.py", "test", TESTS],
            cwd=ROOT / "backend", capture_output=True, text=True, encoding="utf-8",
            errors="replace",
        )
        out = result.stdout + result.stderr
        red = result.returncode != 0
        names = sorted({line.split("]")[-1].strip().split("\n")[0]
                        for line in out.splitlines() if line.startswith("FAIL:")})
        if red:
            print(f"[PASS] {label}")
            for name in names:
                print(f"         caught by: {name}")
        else:
            print(f"[FAIL] {label}: tests still passed, so nothing checks this")
            failed += 1
finally:
    TARGET.write_text(original, encoding="utf-8")
    if TARGET.read_text(encoding="utf-8") != original:
        print("[BAD ] source was not restored byte for byte")
        failed += 1

print()
print("all mutations caught" if failed == 0 else f"{failed} problem(s)")
sys.exit(1 if failed else 0)

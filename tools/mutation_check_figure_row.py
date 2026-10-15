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
        "the gap between figures is guessed at 1/96 inch instead of read off the row",
        "    gap = PREVIEW_FIGURE_GAP_PX * 25.4 / PREVIEW_PX_PER_INCH",
        "    gap = PREVIEW_FIGURE_GAP_PX * 25.4 / 72",
    ),
    (
        "the figure row stops being centred",
        "    table.alignment = WD_TABLE_ALIGNMENT.CENTER\n"
        "    for column, width in zip(table.columns, column_widths):",
        "    table.alignment = WD_TABLE_ALIGNMENT.LEFT\n"
        "    for column, width in zip(table.columns, column_widths):",
    ),
    (
        "columns stop carrying the gap between figures",
        "        column_widths.append(width + gap_mm if column < columns - 1 else width)",
        "        column_widths.append(width)",
    ),
    (
        "the first row is measured as if every picture were on it",
        "    first_row = widths[:columns] if columns else []",
        "    first_row = widths",
    ),
    (
        "figures stop sharing one grid",
        "    if columns > 1 and len(pictures) > 1:",
        "    if columns > 2 and len(pictures) > 1:",
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

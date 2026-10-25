"""Mutation check: the preview-side figure rules must fail when they are undone.

Same idea as ``mutation_check_figure_row.py`` but for the browser half. The
preview and the exported PDF are the same code (``exam-layout.js``), Word is the
Python one, so a rule that only one side implements shows up as a paper that
disagrees with the screen. Each entry below reverts one rule and requires the
named tests to go red; a mutation that stays green proves no test looks at it.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"C:\Users\17321\.minimax\sessions\mvs_fc946281665f4565a3d0ce7af01bd838\workspace\qbj-src")
LAYOUT = ROOT / "frontend" / "exam-layout.js"
LIBRARY = ROOT / "frontend" / "library.js"
TESTS = ["frontend/test_exam_layout.js", "frontend/test_figure_layout_parity.js",
         "frontend/test_library_export_ui.js"]

# (file, label, old, new, tests-that-must-go-red)
MUTATIONS = [
    (
        LAYOUT,
        "the row stops following the position the teacher chose",
        '      if (setting.align) row.style.justifyContent = setting.align;',
        '      if (false) row.style.justifyContent = setting.align;',
        ["frontend/test_exam_layout.js"],
    ),
    (
        LAYOUT,
        "a width typed in the preview never reaches the picture",
        "        if (image && millimetres) image.dataset.figureWidth = String(millimetres);",
        "        if (image && false) image.dataset.figureWidth = String(millimetres);",
        ["frontend/test_exam_layout.js"],
    ),
    (
        LAYOUT,
        "every picture reverts to 150 dpi and the typed width is ignored",
        "        const chosenWidth = Number(image.dataset.figureWidth) || Number(image.dataset.solutionWidth);",
        "        const chosenWidth = Number(image.dataset.solutionWidth);",
        ["frontend/test_figure_layout_parity.js"],
    ),
    (
        LAYOUT,
        "the typed width scales the 150 dpi size instead of replacing it",
        "        const chosenWidth = Number(image.dataset.figureWidth) || Number(image.dataset.solutionWidth);",
        "        const chosenWidth = (Number(image.dataset.figureWidth) || 96 / 150 * image.naturalWidth / MM) || Number(image.dataset.solutionWidth);",
        ["frontend/test_figure_layout_parity.js"],
    ),
    (
        LAYOUT,
        "a forced line break is never inserted",
        "      breaks.forEach(index => {",
        "      breaks.slice(0, 0).forEach(index => {",
        ["frontend/test_exam_layout.js"],
    ),
    (
        LAYOUT,
        "the spacer keeps flex's own row gap as well, so lines sit 28px apart",
        '      row.style.rowGap = "0";',
        '      row.style.rowGap = "14px";',
        ["frontend/test_exam_layout.js", "frontend/test_figure_layout_parity.js"],
    ),
    (
        LAYOUT,
        "the spacer grows to twice the row gap Word separates two lines with",
        "        spacer.style.height = `${FIGURE_GAP_PX}px`;",
        "        spacer.style.height = `${FIGURE_GAP_PX * 2}px`;",
        ["frontend/test_exam_layout.js", "frontend/test_figure_layout_parity.js"],
    ),
    (
        LAYOUT,
        "the spacer's gap stops matching the .qb-figures row gap",
        "  const FIGURE_GAP_PX = 14;",
        "  const FIGURE_GAP_PX = 20;",
        ["frontend/test_figure_layout_parity.js"],
    ),
    (
        LAYOUT,
        "a question's own settings leak onto every other question",
        "      const setting = overrides[question.dataset.questionId];",
        "      const setting = overrides[Object.keys(overrides)[0]];",
        ["frontend/test_exam_layout.js"],
    ),
    (
        LAYOUT,
        "a break before the first figure inserts a spacer for a line that already starts there",
        "      const breaks = (setting.breaks || []).filter(index => index > 0 && index < figures.length).sort((a, b) => a - b);",
        "      const breaks = (setting.breaks || []).filter(index => index >= 0 && index < figures.length).sort((a, b) => a - b);",
        ["frontend/test_exam_layout.js"],
    ),
    (
        LIBRARY,
        "figure adjustments stop being kept when a paper is saved",
        "      figure_overrides: Object.fromEntries(Object.entries(printState.figureOverrides || {}).filter(([id]) => !state.basket || state.basket.includes(id))),",
        "      figure_overrides: {},",
        ["frontend/test_library_export_ui.js"],
    ),
    (
        LIBRARY,
        "a question dropped from the paper keeps its figure settings",
        "    printState.figureOverrides = Object.fromEntries(Object.entries(printState.figureOverrides || {}).filter(([id]) => ids.has(id)));",
        "    pass",
        ["frontend/test_library_export_ui.js"],
    ),
    (
        LIBRARY,
        "reopening a draft forgets every figure adjustment",
        "    printState.figureOverrides = restored.figure_overrides;",
        "    printState.figureOverrides = {};",
        ["frontend/test_library_export_ui.js"],
    ),
    (
        LIBRARY,
        "figure adjustments accept sizes a piece of A4 cannot hold",
        "        if (Number.isInteger(index) && index >= 0 && millimetres >= FIGURE_WIDTH_MIN && millimetres <= FIGURE_WIDTH_MAX) {",
        "        if (Number.isInteger(index) && index >= 0 && millimetres > 0) {",
        ["frontend/test_library_export_ui.js"],
    ),
    (
        LIBRARY,
        "the figure controls never appear for a question that has figures",
        '    box.hidden = !figures.length;',
        '    box.hidden = true;',
        ["frontend/test_library_export_ui.js"],
    ),
    (
        LIBRARY,
        "option pictures are dragged into the figure list too",
        '    return (item.content?.figures || []).filter(figure => figure.slot === "stem");',
        "    return (item.content?.figures || []);",
        ["frontend/test_library_export_ui.js"],
    ),
    (
        LIBRARY,
        "the slider starts somewhere other than where the picture actually is",
        "      const current = chosen ?? printed[index] ?? 40;",
        "      const current = chosen ?? 90;",
        ["frontend/test_library_export_ui.js"],
    ),
    (
        LIBRARY,
        "the slider is seeded from the scaled screen size instead of the printed size",
        "      const millimetres = Math.round(image.offsetWidth * 25.4 / 96 * 10) / 10;",
        "      const millimetres = Math.round(image.getBoundingClientRect().width * 25.4 / 96 * 10) / 10;",
        ["frontend/test_library_export_ui.js"],
    ),
    (
        LIBRARY,
        "the slider only measures the half of a split question that has no figures",
        "    const images = Array.from(ui.paper.querySelectorAll(`.print-question[data-question-id=\"${CSS.escape(id)}\"]`))\n"
        "      .flatMap(block => Array.from(block.querySelectorAll(\".qb-figures .qb-figure img\")));",
        "    const images = Array.from((ui.paper.querySelector(`.print-question[data-question-id=\"${CSS.escape(id)}\"]`) || { querySelectorAll: () => [] })\n"
        "      .querySelectorAll(\".qb-figures .qb-figure img\"));",
        ["frontend/test_library_export_ui.js"],
    ),
    (
        LIBRARY,
        "the first figure offers a line-break switch that can never do anything",
        "    const first = count < 2 || index === 0;",
        "    const first = false;",
        ["frontend/test_library_export_ui.js"],
    ),
    (
        LIBRARY,
        "the exporting state unlocks the first figure's line-break switch",
        "      breaker.disabled = printState.exporting || figures.length < 2 || index === 0;",
        "      breaker.disabled = printState.exporting;",
        ["frontend/test_library_export_ui.js"],
    ),
]


def run(tests):
    for name in tests:
        result = subprocess.run(["node", name], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        if result.returncode != 0:
            return False, name
    return True, ""


originals = {path: path.read_text(encoding="utf-8") for path in {entry[0] for entry in MUTATIONS}}
backup = Path(tempfile.mkdtemp())
for path, _text in originals.items():
    shutil.copy2(path, backup / path.name)


def write(path, text):
    # newline="" keeps each file's own line endings. Without it Windows rewrites
    # every \n as \r\n, and the byte-for-byte restore check below stops meaning
    # anything — read_text normalises both sides to the same thing regardless.
    path.write_text(text, encoding="utf-8", newline="")


failed = 0
try:
    for path, label, old, new, _tests in MUTATIONS:
        source = originals[path]
        if source.count(old) != 1:
            print(f"[BAD ] {label}: pattern found {source.count(old)} times, need exactly 1")
            failed += 1
            continue
        # 每一轮都先把两个文件都放回原样，再只改一处。漏了这一步的话，上一条的改动
        # 会留在磁盘上，下一条无论改的是什么，三个测试里都先看到上一条的错处 ——
        # 报告里写着「被 A 抓到」，实际 A 只是在喊上一条留下的伤口。
        for other, text in originals.items():
            write(other, text)
        write(path, source.replace(old, new))
        green, why = run(TESTS)
        if not green:
            print(f"[PASS] {label}")
            print(f"         caught by: {why}")
        else:
            print(f"[FAIL] {label}: every test still passed, so nothing checks this")
            failed += 1
finally:
    for path in originals:
        write(path, originals[path])
    for path in originals:
        if path.read_bytes() != (backup / path.name).read_bytes():
            print(f"[BAD ] {path.name} was not restored byte for byte")
            failed += 1

print()
print("all mutations caught" if failed == 0 else f"{failed} problem(s)")
sys.exit(1 if failed else 0)

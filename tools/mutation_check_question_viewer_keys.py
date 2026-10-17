"""Mutation check for the full-screen viewer's space-bar zoom and hidden answers.

Two bugs shipped from the same window: pressing space did nothing at all (only
Ctrl+wheel could zoom), and opening a question spilled its answer straight onto
the screen under the stem. Both fixes are keyboard and visibility behaviour, so
the mutations below aim at those two paths — not at wording alone.

Same rules as the other mutation checks: every mutation is reverted before the
next one starts, and a missing anchor stops the run instead of quietly reporting
a clean sweep.

Run with the packaging build Python:

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_question_viewer_keys.py
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

# Windows 控制台默认 cp1252，每条结论要专门一算。据实说就写结论，别拿编码当借口。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
NODE = "node"
VIEWER = ["node", "test_library_question_viewer.js"]
HELP = ["node", "test_shortcut_help.js"]

SPACE_LINE = 'if (event.key === " " || event.code === "Space") { event.preventDefault(); setZoom(zoom + 25); return; }'
FIT_LINE = ('          if (event.key === "0") {\n'
            '            event.preventDefault(); controls.content.classList.remove("native-images"); setZoom(100); return;\n'
            '          }')
FIT_BODY = '            event.preventDefault(); controls.content.classList.remove("native-images"); setZoom(100); return;'
VISIBILITY = "      controls.answerBox.hidden = !hasAnswer; controls.noAnswer.hidden = hasAnswer;"
RESET = "      if (!options.navigationStep) controls.answerBox.open = false;"

# (file, description, original, mutation, command)
MUTATIONS = [
    ("frontend/library-question-viewer.js",
     "pressing space stops enlarging the question",
     f"          {SPACE_LINE}",
     '          if (false) { event.preventDefault(); setZoom(zoom + 25); return; }',
     VIEWER),

    ("frontend/library-question-viewer.js",
     "enlarging no longer holds the page still, so the reader scrolls away",
     f"          {SPACE_LINE}",
     '          if (event.key === " " || event.code === "Space") { setZoom(zoom + 25); return; }',
     VIEWER),

    ("frontend/library-question-viewer.js",
     "space enlarges by a different step than the on-screen plus button",
     f"          {SPACE_LINE}",
     '          if (event.key === " " || event.code === "Space") { event.preventDefault(); setZoom(zoom + 5); return; }',
     VIEWER),

    ("frontend/library-question-viewer.js",
     "space is stolen from buttons and from the answer toggle itself",
     'if (!event.target?.closest?.("button, a[href], summary, [role=\'button\']")) {',
     "if (true) {",
     VIEWER),

    ("frontend/library-question-viewer.js",
     "0 no longer returns to fit width",
     FIT_LINE,
     FIT_LINE.replace('event.key === "0"', 'event.key === "9"'),
     VIEWER),

    ("frontend/library-question-viewer.js",
     "0 fits the text back to width but leaves the pictures oversized",
     FIT_BODY,
     '            event.preventDefault(); setZoom(100); return;',
     VIEWER),

    ("frontend/library-question-viewer.js",
     "the toolbar stops saying that space enlarges the question",
     'more.title = "放大题目（快捷键：空格）";',
     'more.title = "放大题目";',
     VIEWER),

    ("frontend/library-question-viewer.js",
     "the fit button stops saying that 0 does the same thing",
     'fit.title = "适合宽度：题目缩到刚好铺满版心（快捷键 0）";',
     'fit.title = "适合宽度";',
     VIEWER),

    ("frontend/library-question-viewer.js",
     "opening a question leaves focus on the return button, so the first space closes the window",
     "      if (!options.navigationStep) controls.viewport.focus({ preventScroll: true });",
     "      if (!options.navigationStep) controls.exit.focus({ preventScroll: true });",
     VIEWER),

    ("frontend/library-question-viewer.js",
     "the answer is spread under the stem again as soon as a question opens",
     RESET,
     "      if (!options.navigationStep) controls.answerBox.open = true;",
     VIEWER),

    ("frontend/library-question-viewer.js",
     "the answer is never folded away at all",
     RESET,
     "      if (false) controls.answerBox.open = false;",
     VIEWER),

    ("frontend/library-question-viewer.js",
     "turning to the next question folds the answer the reader already opened",
     RESET,
     "      controls.answerBox.open = false;",
     VIEWER),

    ("frontend/library-question-viewer.js",
     "a question with no answer still offers a toggle that opens onto nothing",
     VISIBILITY,
     "      controls.answerBox.hidden = false; controls.noAnswer.hidden = hasAnswer;",
     VIEWER),

    ("frontend/library-question-viewer.js",
     "a question with no answer no longer says so anywhere",
     VISIBILITY,
     "      controls.answerBox.hidden = !hasAnswer; controls.noAnswer.hidden = true;",
     VIEWER),

    ("frontend/library-question-viewer.js",
     "the answer toggle is built but never lands on the reading surface",
     "content.append(question, answerBox, noAnswer, status);",
     "content.append(question, noAnswer, status);",
     VIEWER),

    ("frontend/library-question-viewer.js",
     "the toggle drops the wording the question cards already use",
     'answerToggle.append(node("span", "when-closed", "看答案"), node("span", "when-open", "收起答案"));',
     'answerToggle.append(node("span", "when-closed", "显示答案"), node("span", "when-open", "隐藏答案"));',
     VIEWER),

    ("frontend/shortcut-help.js",
     "the shortcut panel stops telling the reader that space enlarges",
     'row("放大题目", "空格")',
     'row("放大题目", "Space")',
     HELP),

    ("frontend/shortcut-help.js",
     "the question list stops saying how to zoom inside full screen",
     'more: options.fullScreen ? [row("适合宽度", "0")] : [row("全屏看题放大 / 适合宽度", "空格", "0"), row("打开快捷键说明", "?")],',
     'more: options.fullScreen ? [row("适合宽度", "0")] : [row("打开快捷键说明", "?")],',
     HELP),

    ("frontend/shortcut-help.js",
     "the shortcut panel stops saying that the answer waits until asked for",
     'extra: "全屏看题默认把答案收在「看答案」里，点一下才显示；空格放大、0 回到适合宽度。" + ',
     'extra: "全屏看题里可以用键盘切题。" + ',
     HELP),
]


def run(command):
    return subprocess.run(command, cwd=str(FRONTEND), capture_output=True, text=True, encoding="utf-8", errors="replace")


def main():
    results = []
    for relative, description, original, mutation, command in MUTATIONS:
        target = ROOT / relative
        source = target.read_text(encoding="utf-8")
        if original not in source:
            raise SystemExit(f"{relative}: anchor not found, the check has gone stale:\n{original}")
        baseline = run(command)
        if baseline.returncode != 0:
            print(baseline.stdout[-3000:], baseline.stderr[-3000:])
            raise SystemExit(f"{relative}: the unmutated suite already fails, refusing to trust this run")
        target.write_text(source.replace(original, mutation, 1), encoding="utf-8")
        try:
            broken = run(command)
        finally:
            # Revert before the next mutation, not after the whole loop: otherwise
            # every later mutation is measured on top of the previous broken state.
            target.write_text(source, encoding="utf-8")
        caught = broken.returncode != 0
        results.append((description, caught))
        print(("挡住 ✓  " if caught else "漏过 ✗  ") + description)
        if not caught:
            print((broken.stdout or "")[-2000:] + (broken.stderr or "")[-2000:])
    survived = [item for item in results if not item[1]]
    print(f"\n{len(results) - len(survived)}/{len(results)} 条变异均被挡住" if not survived
          else f"\n{len(survived)} 条变异漏过：{survived}")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())

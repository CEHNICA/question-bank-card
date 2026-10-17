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
LIBRARY = ["node", "test_shortcut_help.js"]
HELP = ["node", "test_shortcut_help.js"]

SPACE_LINE = 'if (event.key === " " || event.code === "Space") { event.preventDefault(); close(); return; }'
LIST_SPACE = ('    if (ordinary && !event.shiftKey && (event.key === " " || event.code === "Space")\n'
              '        && ui.sheet.hidden && !document.querySelector("dialog[open]")) {')
LIST_CURRENT = '      const current = state.inspectorId ? state.catalog.get(state.inspectorId) : null;'
LIST_OPEN = '        openQuestionViewer(current, document.getElementById(`q-${current.id}`)?.querySelector(".library-full-button"));'
LIST_TARGET = "if (current && !event.target?.closest?.(\"button, a[href], summary, [role='button']\")) {"
LIST_BLOCK = LIST_SPACE + "\n" + LIST_CURRENT + '\n      if (current && !event.target?.closest?.("button, a[href], summary, [role=\'button\']")) {\n        event.preventDefault();\n' + LIST_OPEN + "\n      }"
FIT_LINE = ('          if (event.key === "0") {\n'
            '            event.preventDefault(); controls.content.classList.remove("native-images"); setZoom(100); return;\n'
            '          }')
FIT_BODY = '            event.preventDefault(); controls.content.classList.remove("native-images"); setZoom(100); return;'
VISIBILITY = "      controls.answerBox.hidden = !hasAnswer; controls.noAnswer.hidden = hasAnswer;"
RESET = "      if (!options.navigationStep) controls.answerBox.open = false;"

# (file, description, original, mutation, command)
MUTATIONS = [
    ("frontend/library-question-viewer.js",
     "pressing space inside full screen no longer leaves",
     f"          {SPACE_LINE}",
     '          if (false) { event.preventDefault(); close(); return; }',
     VIEWER),

    ("frontend/library-question-viewer.js",
     "leaving by space lets the page scroll away underneath",
     f"          {SPACE_LINE}",
     '          if (event.key === " " || event.code === "Space") { close(); return; }',
     VIEWER),

    ("frontend/library-question-viewer.js",
     "space enlarges the question instead of leaving it",
     f"          {SPACE_LINE}",
     '          if (event.key === " " || event.code === "Space") { event.preventDefault(); setZoom(zoom + 25); return; }',
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
     "the toolbar claims space is how you enlarge the question",
     'more.title = "放大题目（也可以按住 Ctrl 再滚滚轮）";',
     'more.title = "放大题目（快捷键：空格）";',
     VIEWER),

    ("frontend/library-question-viewer.js",
     "the fit button stops saying that 0 does the same thing",
     'fit.title = "适合宽度：题目缩到刚好铺满版心（快捷键 0）";',
     'fit.title = "适合宽度";',
     VIEWER),

    ("frontend/library-question-viewer.js",
     "the return button stops saying that space also gets you out",
     'button("返回题库 · Esc / 空格", "questionViewerClose", close)',
     'button("返回题库 · Esc", "questionViewerClose", close)',
     VIEWER),

    ("frontend/library-question-viewer.js",
     "opening a question leaves focus on the return button, so space presses that button instead of leaving",
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
     "the shortcut panel stops saying that the answer waits until asked for",
     'extra: "空格是全屏看题的开关：题库页按它打开当前选中题，进去以后再按一下退回来。放大用工具栏的＋／－、Ctrl＋滚轮或 0。答案默认收在「看答案」里，点一下才显示。" + ',
     'extra: "空格是全屏看题的开关：题库页按它打开当前选中题，进去以后再按一下退回来。" + ',
     HELP),

    # 下面几条盯的是「在题库页按空格进全屏看题」。以前这一页只认「/」和「?」，
    # 空格按下去什么也不会发生，看着像软件没反应。
    ("frontend/library.js",
     "pressing space on the question list no longer opens full screen",
     LIST_BLOCK,
     LIST_SPACE.replace('event.key === " "', 'event.key === "\\u000b"') + "\n" + LIST_CURRENT + "\n      return;\n    }",
     LIBRARY),

    ("frontend/library.js",
     "space opens full screen for some question other than the selected one",
     LIST_CURRENT,
     '      const current = state.items?.[0] ? state.catalog.get(state.items[0].id) : null;',
     LIBRARY),

    ("frontend/library.js",
     "space opens full screen even when nothing is selected, picking for the reader",
     LIST_TARGET,
     '      if (current) {',
     LIBRARY),

    ("frontend/library.js",
     "space is stolen from buttons and from the card's own More menu",
     LIST_TARGET,
     '      if (current || true) {',
     LIBRARY),

    ("frontend/library.js",
     "opening full screen by space lets the list scroll away underneath",
     "        event.preventDefault();\n" + LIST_OPEN,
     LIST_OPEN,
     LIBRARY),

    ("frontend/library.js",
     "space ignores the modifier keys and reaches for full screen during typing",
     LIST_SPACE,
     '    if (ordinary && (event.key === " " || event.code === "Space")\n        && ui.sheet.hidden && !document.querySelector("dialog[open]")) {',
     LIBRARY),

    ("frontend/shortcut-help.js",
     "the question list stops saying that space opens full screen",
     'row("全屏看题（当前选中题）", "空格")',
     'row("全屏看题（当前选中题）", "Space")',
     HELP),

    ("frontend/shortcut-help.js",
     "the question list claims space is not a shortcut at all",
     'primary: options.fullScreen ? [row("上一题 / 下一题", "←", "→"), row("返回题库", "Esc", "空格"), row("看题缩放", "Ctrl+滚轮"), row("适合宽度", "0")]\n'
     '        : [row("搜索题目", "/"), row("全屏看题（当前选中题）", "空格"),',
     'primary: options.fullScreen ? [row("上一题 / 下一题", "←", "→"), row("返回题库", "Esc", "空格"), row("看题缩放", "Ctrl+滚轮"), row("适合宽度", "0")]\n'
     '        : [row("搜索题目", "/"),',
     HELP),

    ("frontend/shortcut-help.js",
     "the shortcut panel stops telling the reader that space gets you back out",
     'row("返回题库", "Esc", "空格")',
     'row("返回题库", "Esc")',
     HELP),

    ("frontend/shortcut-help.js",
     "the shortcut panel stops offering 0 to go back to fit width",
     'row("看题缩放", "Ctrl+滚轮"), row("适合宽度", "0")]',
     'row("看题缩放", "Ctrl+滚轮")]',
     HELP),

    ("frontend/shortcut-help.js",
     "the shortcut panel stops calling space a toggle",
     'extra: "空格是全屏看题的开关：题库页按它打开当前选中题，进去以后再按一下退回来。',
     'extra: "题库页和全屏看题里可以按空格。',
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

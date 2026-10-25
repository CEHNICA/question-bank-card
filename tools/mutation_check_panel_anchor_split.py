"""Mutation check for the advanced-filter panel anchor and the tag/answer split (1.25.23).

The teacher sent two screenshots and said, in effect:

  1. 点开更多筛选，你看看，这内容都出了屏幕了都
  2. 这么多按钮无非分成两类，一个是处理答案的，一个是处理标签的。给它分为别类，放清楚

Both are regressions or gaps this check pins:

  · The panel used to be anchored to the More Filters button (`right: 0`, with
    `.library-advanced` as the containing block). 1.25.22 moved that button next to
    来源, so the panel followed it 700px to the left and landed at x=-276 — measured,
    276px of it off the left edge, with 题型/排序/有没有答案/标签关系 all cut in half.
    The anchor now lives on the filter row, and the panel is never wider than it.
  · The five buttons were one flat list. 「补齐缺的参考答案」 and 「补齐缺的知识点标签」
    looked identical, and 「只处理当前这批 · 807 题」 did not say what it touched at all.
    They are now two columns: 标签 and 答案.
  · The three spending buttons carry data-cost="ai" and get a small 「AI」 chip, so
    the cost sits on the button instead of only in one note above the list.

Four mutations whose anchors went stale in 1.25.23 moved here from
tools/mutation_check_todo_cost_split.py (the filter container's class, the two group
headings that became the AI chip, and the paying-button border selector). The old
script carries a RETIRED guard so they cannot silently come back there.

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_panel_anchor_split.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
COMMAND = ["node", "--test", "test_library_todo_merge.js", "test_library_context_sidebar.js",
           "test_library_filter_bar.js", "test_library_compact.js"]

LIBRARY_JS = "frontend/library.js"
LIBRARY_CSS = "frontend/library.css"

# ── 面板锚点 ────────────────────────────────────────────────────────────────
ADVANCED_RULE = '.library-advanced { flex: none; font-size: 13px; color: var(--ink-2); }\n'
ADVANCED_POSITIONED = '.library-advanced { position: relative; flex: none; font-size: 13px; color: var(--ink-2); }\n'
PANEL_ANCHOR = 'top: calc(100% + 7px); left: 0; right: auto;\n'
PANEL_ANCHOR_RIGHT = 'top: calc(100% + 7px); right: 0;\n'
PANEL_WIDTH = '  width: min(680px, 100%); margin: 0; padding: 14px;\n'
PANEL_WIDTH_VIEWPORT = '  width: min(680px, calc(100vw - 32px)); margin: 0; padding: 14px;\n'
PANEL_NARROW = '  .library-advanced[open] > .library-filters { width: min(520px, 100%); padding: 11px; flex-wrap: wrap; }\n'
PANEL_NARROW_VIEWPORT = ('  .library-advanced[open] > .library-filters '
                        '{ width: min(520px, calc(100vw - 24px)); padding: 11px; flex-wrap: wrap; }\n')

# ── 分栏 ────────────────────────────────────────────────────────────────────
NOTE_LINE = '    body.append(node("p", "library-todo-note", apiReady\n'
NOTE_LINE_GONE = ''
COLS = '    const cols = node("div", "library-todo-cols");\n'
COLS_GONE = ''
SOLO = '    if (!answerActions.length) cols.classList.add("library-todo-cols-solo");\n'
SOLO_GONE = ''
TAG_COLUMN = '    cols.append(column("标签", [\n'
TAG_COLUMN_GONE = ''
ANSWER_COLUMN = '      cols.append(column("答案", [paidGroup(answerActions)]));\n'
ANSWER_COLUMN_GONE = ''

TAG_COST = '      tags.dataset.cost = "ai";\n'
CURRENT_COST = '      current.dataset.cost = "ai";\n'
ANSWER_COST = '      answers.dataset.cost = "ai";\n'

# 从 1.25.21 迁过来的四条（锚点在本版改措辞/改结构时过期了，检查本身没作废）
LOOK_CONTAINER = '      const g = node("div", "library-todo-look");\n'
LOOK_CONTAINER_BUG = '      const g = node("div", "library-tool-actions library-todo-look");\n'
PAID_BORDER = ('.library-todo-body .library-tool-actions > .library-tool-action '
               '{ border-color: var(--line-strong); }\n')
PAID_BORDER_FLAT = ('.library-todo-body .library-tool-actions > .library-tool-action '
                    '{ border-color: transparent; }\n')

CSS_COLS = ('.library-todo-cols { display: grid; grid-template-columns: '
            'repeat(2, minmax(0, 1fr)); gap: 14px; }\n')
CSS_COLS_ONE = '.library-todo-cols { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; }\n'
CSS_SOLO = '.library-todo-cols-solo { grid-template-columns: minmax(0, 1fr); }\n'
CSS_SOLO_GONE = ''
CSS_SPLIT = '.library-todo-col + .library-todo-col { padding-left: 14px; border-left: 1px solid var(--line); }\n'
CSS_SPLIT_GONE = ''
CSS_CHIP = ('.library-todo-body .library-tool-action[data-cost="ai"]::after {\n'
            '  content: "AI"; flex: 0 0 auto; margin-left: auto; padding: 1px 6px; border-radius: 99px;\n'
            '  background: var(--accent-soft); color: var(--accent-dark); font-size: 10.5px; '
            'font-weight: 700; line-height: 1.6;\n}\n')
CSS_CHIP_GONE = ''

# (relative, description, original, mutation)
MUTATIONS = [
    (LIBRARY_CSS,
     "「更多筛选」becomes its own positioning context again — the panel follows the button, "
     "and 1.25.22 proved exactly where that lands: x=-276, half the panel off-screen",
     ADVANCED_RULE, ADVANCED_POSITIONED),

    (LIBRARY_CSS,
     "the panel is anchored back to the button's right edge instead of the filter row",
     PANEL_ANCHOR, PANEL_ANCHOR_RIGHT),

    (LIBRARY_CSS,
     "the panel stops being bounded by the filter row and sizes off the viewport again",
     PANEL_WIDTH, PANEL_WIDTH_VIEWPORT),

    (LIBRARY_CSS,
     "the narrow-screen panel sizes off the viewport again, so it can hang past the edge",
     PANEL_NARROW, PANEL_NARROW_VIEWPORT),

    (LIBRARY_JS,
     "the 标签 column disappears",
     TAG_COLUMN, TAG_COLUMN_GONE),

    (LIBRARY_JS,
     "the 答案 column disappears — the two kinds of work are flat again",
     ANSWER_COLUMN, ANSWER_COLUMN_GONE),

    (LIBRARY_JS,
     "the whole split block is gone and the panel has no columns at all",
     COLS, COLS_GONE),

    (LIBRARY_JS,
     "the solo-column rule is dropped, so a 标签-only panel leaves half a row of blank",
     SOLO, SOLO_GONE),

    (LIBRARY_JS,
     "the note explaining what costs money is gone",
     NOTE_LINE, NOTE_LINE_GONE),

    (LIBRARY_JS,
     "补齐缺的知识点标签 loses its AI marker — the cost is back to one line of small text",
     TAG_COST, ''),

    (LIBRARY_JS,
     "只处理当前这批 loses its AI marker, and nothing says which kind of work it is",
     CURRENT_COST, ''),

    (LIBRARY_JS,
     "补齐缺的参考答案 loses its AI marker",
     ANSWER_COST, ''),

    (LIBRARY_CSS,
     "the AI chip stops being drawn",
     CSS_CHIP, CSS_CHIP_GONE),

    (LIBRARY_CSS,
     "the two columns stop sitting side by side",
     CSS_COLS, CSS_COLS_ONE),

    (LIBRARY_CSS,
     "the solo-column rule is dropped from the stylesheet",
     CSS_SOLO, CSS_SOLO_GONE),

    (LIBRARY_CSS,
     "the divider between the two columns disappears",
     CSS_SPLIT, CSS_SPLIT_GONE),

    # ── 从 tools/mutation_check_todo_cost_split.py 迁过来的四条 ──
    (LIBRARY_JS,
     "the filter container takes .library-tool-actions back — AI queues, the two free "
     "filters go grey for the whole run",
     LOOK_CONTAINER, LOOK_CONTAINER_BUG),

    (LIBRARY_CSS,
     "the paying buttons lose their solid border and all five look identical again",
     PAID_BORDER, PAID_BORDER_FLAT),

]


def apply(source: str, original: str, mutation: str) -> str:
    """Swap one anchor, matching \\n or \\r\\n so a mixed-ending tree still works."""
    if original in source:
        return source.replace(original, mutation, 1)
    crlf_anchor = original.replace("\n", "\r\n")
    if crlf_anchor in source:
        return source.replace(crlf_anchor, mutation.replace("\n", "\r\n"), 1)
    raise SystemExit(f"anchor not found, the check has gone stale:\n{original!r}")


def run() -> subprocess.CompletedProcess:
    return subprocess.run(COMMAND, cwd=str(FRONTEND), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main() -> int:
    results = []
    for relative, description, original, mutation in MUTATIONS:
        target = ROOT / relative
        raw = target.read_bytes()
        source = raw.decode("utf-8")
        try:
            mutated = apply(source, original, mutation)
        except SystemExit as error:
            raise SystemExit(f"{relative}: {error}") from None
        if mutated == source:
            raise SystemExit(f"{relative}: anchor not found, the check has gone stale:\n{original!r}")
        baseline = run()
        if baseline.returncode != 0:
            print(baseline.stdout[-3000:], baseline.stderr[-3000:])
            raise SystemExit(f"{relative}: the unmutated suite already fails, refusing to trust this run")
        target.write_bytes(mutated.encode("utf-8"))
        try:
            broken = run()
        finally:
            target.write_bytes(raw)
        if target.read_bytes() != raw:
            raise SystemExit(f"{relative}: restoring by bytes did not give the original file back")
        caught = broken.returncode != 0
        results.append((description, caught))
        print(("挡住 OK   " if caught else "漏过 FAIL ") + description)
        if not caught:
            print((broken.stdout or "")[-2000:] + (broken.stderr or "")[-2000:])
    survived = [item for item in results if not item[1]]
    print(f"\n{len(results) - len(survived)}/{len(results)} 条变异均被挡住" if not survived
          else f"\n{len(survived)} 条变异漏过：{survived}")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())

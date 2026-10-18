"""Mutation check for the pending-work panel and the filter row (1.25.21).

The teacher said 「我总感觉还是有点问题」 without being able to say where. Measured
on the real app, four things were wrong:

  · AI 一登记，两个"不花钱的筛选"跟着变灰. Two separate loops lock overlapping sets
    of buttons by different rules; the later one wins. The 1.25.20 test only asserted
    that the line locking the filters on its own *existed* — it did, and the bug was
    still there. The net effect is what matters, so the anchor here is the class on the
    filter container: applyBatchButtonState selects by .library-tool-actions, so whether
    it sweeps the filters in depends only on that.
  · Five buttons that all looked identical — same size, same radius, transparent
    background, and a border that was transparent too. Only a leading 「+」 told
    「只看」 apart from 「让 AI 去干」.
  · The collapsed summary said 「建议待采纳」/「没打标签」 while the buttons right below
    said 「待核对」/「未标知识点」. One thing, two names, 30px apart. The third item was
    「AI 能补 3 项」 — how many buttons sit below, not how much work is left.
  · 「只处理当前这批」 with nothing filtered queues 807 questions — 807 AI calls — and
    its note only said 「每道题调用一次 AI，会消耗额度」. The cost was never a number.

Two things were checked and deliberately left alone: 「未标知识点」 is precise (tags
come in four kinds and this filter only counts the knowledge one), and the basket icon
is a basket once you blow it up.

Same rules as the other checks: revert before the next mutation, a missing anchor
stops the run instead of reporting a clean sweep, and everything goes through bytes
because this tree mixes LF and CRLF.

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_todo_cost_split.py
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

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
LIBRARY_READING_CSS = "frontend/library-reading.css"

LOOK_CONTAINER = '      const look = node("div", "library-todo-look");'
LOOK_CONTAINER_BUG = '      const look = node("div", "library-tool-actions library-todo-look");'

LOOK_LOCK = ('    for (const control of document.querySelectorAll("#libraryTodos '
             '.library-todo-look button")) control.disabled = selectedView;\r\n')
LOOK_LOCK_LAST = '    applyBatchButtonState();\r\n'
ORDER_SWAPPED = (LOOK_LOCK_LAST + LOOK_LOCK)

PENDING_BIT = '    if (pendingCount) bits.push(`${pendingCount} 题等你核对`);\r\n'
PENDING_BIT_OLD = '    if (pendingCount) bits.push(`${pendingCount} 题建议待采纳`);\r\n'
UNTAGGED_BIT = '    if (missingCount) bits.push(`${missingCount} 题没知识点`);\r\n'
UNTAGGED_BIT_OLD = '    if (missingCount) bits.push(`${missingCount} 题没打标签`);\r\n'
ANSWER_BIT = '    if (answerCount) bits.push(`${answerCount} 题没答案`);\r\n'

FREE_HEAD = '      body.append(node("p", "library-todo-head library-todo-head-free", "只看这些题 · 不花钱"), look);'
FREE_HEAD_GONE = '      body.append(look);'
COST_HEAD = '        ? "让 AI 逐题处理 · 每题消耗一次额度"\r\n'
COST_HEAD_BARE = '        ? "让 AI 逐题处理"\r\n'

HOVER_COST = '          + `点下去会调用 AI ${Number(state.total) || 0} 次（每道题一次），会消耗额度。`\r\n'
HOVER_COST_VAGUE = '          + "每道题调用一次 AI，会消耗额度。"\r\n'
CONFIRM_COST = '            + `这一次会调用 AI ${count} 次（每道题一次），会消耗额度。生成的标签只是建议，不会直接贴到题目上。`\r\n'
CONFIRM_COST_VAGUE = '            + "每道题调用一次 AI，会消耗额度。生成的标签只是建议，不会直接贴到题目上。"\r\n'

FREE_BORDER = '.library-todo-look .library-tool-action { border-color: transparent; }\n'
PAID_BORDER = ('.library-todo-body > .library-tool-actions > .library-tool-action '
               '{ border-color: var(--line-strong); }\n')
PAID_BORDER_FLAT = ('.library-todo-body > .library-tool-actions > .library-tool-action '
                    '{ border-color: transparent; }\n')

FILTER_COLUMNS = '    display: grid; grid-template-columns: auto auto;\n'
FILTER_COLUMNS_GAP = '    display: grid; grid-template-columns: minmax(0, 1fr) auto;\n'
FILTER_COLUMNS_GAP_AREAS = (FILTER_COLUMNS_GAP
                            + '    grid-template-areas: "source advanced" "types types";\n')
FACET_LABEL = ('  .library-page .library-facet-label { padding-top: 3px; white-space: nowrap; }\n')
FACET_SLOT = ('  .library-page .library-filterbar > .library-facet '
              '{ grid-area: types; min-width: 0; }\n')
NARROW_3COL = ('    .library-page .library-filterbar { grid-template-columns: minmax(0, 1fr) '
               'auto auto; gap: 12px 6px; padding: 10px 10px 0; }\n')
NARROW_2COL = ('    .library-page .library-filterbar { grid-template-columns: minmax(0, 1fr) '
               'auto; gap: 12px 6px; padding: 10px 10px 0; }\n')

# (relative, description, original, mutation)
MUTATIONS = [
    (LIBRARY_JS,
     "the filter container takes .library-tool-actions back — the exact 1.25.20 bug: "
     "AI queues, applyBatchButtonState sweeps the two free filters in, and they go grey "
     "for the whole run while AI is working on them",
     LOOK_CONTAINER, LOOK_CONTAINER_BUG),

    (LIBRARY_JS,
     "applyBatchButtonState runs before the filters are locked, so its queueBusy rule wins again",
     LOOK_LOCK + LOOK_LOCK_LAST, ORDER_SWAPPED),

    (LIBRARY_JS,
     "the summary goes back to saying 建议待采纳 while the button says 待核对",
     PENDING_BIT, PENDING_BIT_OLD),

    (LIBRARY_JS,
     "the summary goes back to saying 没打标签 while the button says 未标知识点",
     UNTAGGED_BIT, UNTAGGED_BIT_OLD),

    (LIBRARY_JS,
     "the third number stops being real work and goes back to counting buttons below it",
     ANSWER_BIT, ''),

    # 上一版（tools\mutation_check_todo_merge.py）里有三条「整行删掉」的变异，锚点
    # 随这次改措辞一起过期，被搬到这里。搬的时候必须连搬，否则是悄悄少测了三条。
    (LIBRARY_JS,
     "the entry stops counting how many suggestions are waiting at all",
     PENDING_BIT, ''),

    (LIBRARY_JS,
     "the entry stops counting how many questions have no knowledge tag",
     UNTAGGED_BIT, ''),

    (LIBRARY_JS,
     "the free group loses its visible heading, so nothing on screen says these cost nothing",
     FREE_HEAD, FREE_HEAD_GONE),

    (LIBRARY_JS,
     "the spending heading drops the cost, leaving a warning nobody sees without hovering",
     COST_HEAD, COST_HEAD_BARE),

    (LIBRARY_JS,
     "the hover stops counting AI calls — 807 calls reads like a small job again",
     HOVER_COST, HOVER_COST_VAGUE),

    (LIBRARY_JS,
     "the confirmation, the last chance to catch a mistake, stops counting AI calls",
     CONFIRM_COST, CONFIRM_COST_VAGUE),

    (LIBRARY_CSS,
     "the paying buttons lose their solid border and all five look identical again",
     PAID_BORDER, PAID_BORDER_FLAT),

    (LIBRARY_CSS,
     "the free filters stop being borderless text and match the paying buttons",
     FREE_BORDER, ''),

    (LIBRARY_READING_CSS,
     "the first column goes back to 1fr: 来源 and 更多筛选 drift 777px apart again",
     FILTER_COLUMNS, FILTER_COLUMNS_GAP),

    (LIBRARY_READING_CSS,
     "the dead 'types types' row comes back and the filter bar silently regains 15px of nothing",
     FILTER_COLUMNS, FILTER_COLUMNS_GAP_AREAS),

    (LIBRARY_READING_CSS,
     "a full-width row is reserved again for a facet that lives one level deeper and "
     "never lands in it",
     FACET_LABEL, FACET_SLOT + FACET_LABEL),

    (LIBRARY_READING_CSS,
     "the 559px layout reserves a third column for a control that no longer exists",
     NARROW_2COL, NARROW_3COL),
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

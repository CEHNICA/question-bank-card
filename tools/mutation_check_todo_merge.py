"""Mutation check for the merged pending-work entry and the always-on tag-suggestion button (1.25.20).

The teacher could not find the button that asks AI for tag suggestions. Measured:
it only appeared after ticking questions, and the only entry before that read
「批量补标签和答案　要花额度　3 项」 — a warning where the action should have been,
with no part of the page ever saying 「让 AI 提标签建议」. Two more jobs sat on a
different row in a different shape. Now they share one entry that counts all three,
and the suggestion button is always on the page.

Same rules as the other checks: revert before the next mutation, a missing anchor
stops the run instead of reporting a clean sweep, and everything goes through bytes
because this tree mixes LF and CRLF.

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_todo_merge.py
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

LIBRARY_HTML = "frontend/library.html"
LIBRARY_JS = "frontend/library.js"
LIBRARY_CSS = "frontend/library.css"
LIBRARY_READING_CSS = "frontend/library-reading.css"

BULK_OPEN = '        <div class="library-bulk" aria-label="题目列表操作">'
SUGGEST_SLOT = ('          <div id="suggestTagsSlot" class="library-suggest-slot">\n'
                '            <button id="generateSelectedTags" class="button button-quiet button-small" type="button" disabled\n'
                '              title="先在左边勾几道题，再点这个让 AI 给它们提标签建议">AI 建议标签</button>\n'
                '          </div>')
SUGGEST_IN_BULK = ('            <button id="generateSelectedTags" class="button button-quiet button-small" type="button" disabled\n'
                   '              title="先在左边勾几道题，再点这个让 AI 给它们提标签建议">AI 建议标签</button>')
BULK_TODOS = '          <div id="libraryTodos" class="library-todos" role="group" aria-label="还有活没干" hidden></div>'
SUGGEST = ('            <button id="generateSelectedTags" class="button button-quiet button-small" type="button" disabled\n'
           '              title="先在左边勾几道题，再点这个让 AI 给它们提标签建议">AI 建议标签</button>')
SUGGEST_HIDDEN = ('            <button id="generateSelectedTags" class="button button-quiet button-small" type="button" hidden disabled\n'
                 '              title="先在左边勾几道题，再点这个让 AI 给它们提标签建议">AI 建议标签</button>')
SUGGEST_NO_TITLE = ('            <button id="generateSelectedTags" class="button button-quiet button-small" type="button" disabled>'
                    'AI 建议标签</button>')
SYNC_SHOW = '    suggest.hidden = false;'
COST_BADGE = '    summary.append(node("span", "library-batch-cost", "要花额度"));\n'
LEAD_LINE = '    summary.append(node("span", "library-todo-lead", working ? "AI 正在处理" : "还有活没干"));\n'
PENDING_BIT = '    if (pendingCount) bits.push(`${pendingCount} 题建议待采纳`);\n'
UNTAGGED_BIT = '    if (missingCount) bits.push(`${missingCount} 题没打标签`);\n'
AI_BIT = '    if (batchActions.length) bits.push(`AI 能补 ${batchActions.length} 项`);\n'
BATCH_SELECTOR = 'document.querySelectorAll("#libraryTodos .library-tool-actions button")'
BATCH_SELECTOR_ALL = 'document.querySelectorAll("#libraryTodos button")'
TITLE_OFF = '题库里没有开启「AI 建议标签」这项功能，要到「设置」里打开才能用。'
TITLE_OFF_BAD = '现在不能使用。'
TITLE_COUNTED = '      ? `让 AI 给勾选的 ${count} 道题生成标签建议。生成的只是建议，要你逐条核对确认后才贴到题目上。每道题调用一次 AI，会消耗额度。`'
TITLE_UNCOUNTED = '      ? "让 AI 给勾选的题目生成标签建议。生成的只是建议，要你逐条核对确认后才贴到题目上。每道题调用一次 AI，会消耗额度。"'
READY_LINE = '    return state.features.knowledge_tags === true && state.ai.mode === "api" && state.ai.api_ready === true;'
READY_LOOSE = '    return state.features.knowledge_tags === true;'
AREAS = '    grid-template-areas: "source advanced" "types types";'
AREAS_DEAD = '    grid-template-areas: "source sort quick advanced" "types types types types";'

# (relative, description, original, mutation)
MUTATIONS = [
    (LIBRARY_HTML,
     "the pending-work entry goes back to the filter row, next to 来源",
     BULK_TODOS, ""),

    (LIBRARY_HTML,
     "「AI 建议标签」 goes back inside .bulk-actions, which is hidden until something is ticked — "
     "the markup and the sync are both still correct, the button is simply gone again",
     SUGGEST_SLOT,
     '          <div class="bulk-actions">\n'
     '            <button id="generateSelectedTags" class="button button-quiet button-small" type="button" disabled\n'
     '              title="先在左边勾几道题，再点这个让 AI 给它们提标签建议">AI 建议标签</button>\n'
     '          </div>'),

    (LIBRARY_HTML,
     "「AI 建议标签」 is hidden in the markup again, so the first frame has no such button",
     SUGGEST, SUGGEST_HIDDEN),

    (LIBRARY_HTML,
     "「AI 建议标签」 loses its hover note, leaving a grey button that says nothing",
     SUGGEST, SUGGEST_NO_TITLE),

    (LIBRARY_JS,
     "each refresh hides the tag-suggestion button again",
     SYNC_SHOW, "    suggest.hidden = true;"),

    (LIBRARY_JS,
     "the cost warning takes over the entry's name again, so nobody can tell what it does",
     LEAD_LINE, LEAD_LINE + COST_BADGE),

    (LIBRARY_JS,
     "the entry stops counting how many suggestions are waiting",
     PENDING_BIT, ""),

    (LIBRARY_JS,
     "the entry stops counting how many questions have no tag",
     UNTAGGED_BIT, ""),

    (LIBRARY_JS,
     "the entry stops saying how many jobs AI can fill in — the very thing being hunted for",
     AI_BIT, ""),

    (LIBRARY_JS,
     "locking the batch buttons one-size-fits-all also locks the two filters, and they stop responding",
     BATCH_SELECTOR, BATCH_SELECTOR_ALL),

    (LIBRARY_JS,
     "the hover note stops naming the settings page, leaving 'unavailable' with nowhere to go",
     TITLE_OFF, TITLE_OFF_BAD),

    (LIBRARY_JS,
     "the hover note drops how many questions were ticked",
     TITLE_COUNTED, TITLE_UNCOUNTED),

    (LIBRARY_JS,
     "the button turns clickable even though the API was never configured",
     READY_LINE, READY_LOOSE),

    (LIBRARY_READING_CSS,
     "the two vacated grid columns come back and leave a gap between 来源 and 更多筛选",
     AREAS, AREAS_DEAD),
]


def apply(source: str, original: str, mutation: str) -> str:
    """Swap one anchor, matching \\n or \\r\\n so a mixed-ending tree still works."""
    if original in source:
        return source.replace(original, mutation, 1)
    crlf_anchor = original.replace("\n", "\r\n")
    if crlf_anchor in source:
        return source.replace(crlf_anchor, mutation.replace("\n", "\r\n"), 1)
    raise SystemExit(f"anchor not found, the check has gone stale:\n{original}")


def run() -> subprocess.CompletedProcess:
    return subprocess.run(COMMAND, cwd=str(FRONTEND), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main() -> int:
    results = []
    for relative, description, original, mutation in MUTATIONS:
        target = ROOT / relative
        raw = target.read_bytes()
        source = raw.decode("utf-8")
        mutated = apply(source, original, mutation)
        if mutated == source:
            raise SystemExit(f"{relative}: anchor not found, the check has gone stale:\n{original}")
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
        print(("挡住 ✓  " if caught else "漏过 ✗  ") + description)
        if not caught:
            print((broken.stdout or "")[-2000:] + (broken.stderr or "")[-2000:])
    survived = [item for item in results if not item[1]]
    print(f"\n{len(results) - len(survived)}/{len(results)} 条变异均被挡住" if not survived
          else f"\n{len(survived)} 条变异漏过：{survived}")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())

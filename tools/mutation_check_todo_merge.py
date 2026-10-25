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

LIBRARY_HTML = "frontend/library.html"
LIBRARY_JS = "frontend/library.js"
LIBRARY_CSS = "frontend/library.css"
LIBRARY_READING_CSS = "frontend/library-reading.css"

BULK_OPEN = '        <div class="library-bulk" aria-label="题目列表操作">'
SUGGEST_SLOT = ('          <div id="suggestTagsSlot" class="library-suggest-slot">\n'
                '            <button id="generateSelectedTags" class="button button-quiet button-small" type="button" disabled\n'
                '              title="先在左边勾几道题，再点这个让 AI 给它们生成标签建议">批量生成标签建议</button>\n'
                '            <span id="suggestTagsHint" class="library-suggest-hint" role="status" hidden></span>\n'
                '          </div>')
SUGGEST_IN_BULK = ('            <button id="generateSelectedTags" class="button button-quiet button-small" type="button" disabled\n'
                   '              title="先在左边勾几道题，再点这个让 AI 给它们生成标签建议">批量生成标签建议</button>')
BULK_TODOS = '          <div id="libraryTodos" class="library-todos" role="group" aria-label="还有活没干" hidden></div>'
SUGGEST = ('            <button id="generateSelectedTags" class="button button-quiet button-small" type="button" disabled\n'
           '              title="先在左边勾几道题，再点这个让 AI 给它们生成标签建议">批量生成标签建议</button>')
SUGGEST_HIDDEN = ('            <button id="generateSelectedTags" class="button button-quiet button-small" type="button" hidden disabled\n'
                 '              title="先在左边勾几道题，再点这个让 AI 给它们生成标签建议">批量生成标签建议</button>')
SUGGEST_NO_TITLE = ('            <button id="generateSelectedTags" class="button button-quiet button-small" type="button" disabled>'
                    '批量生成标签建议</button>')
# 1.25.91：按钮旁边那行常驻提示。悬停说明看不见就等于没说，
# 光把 title 写细了不算修好 —— 得有一行看得见的字。
SUGGEST_HINT = ('            <span id="suggestTagsHint" class="library-suggest-hint" role="status" hidden></span>')
HINT_NO_SPAN = '            <span id="suggestTagsHint" class="library-suggest-hint" role="status" hidden></span>'
SYNC_SHOW = '    suggest.hidden = false;'
COST_BADGE = '    summary.append(node("span", "library-batch-cost", "要花额度"));\n'
LEAD_LINE = '    summary.append(node("span", "library-todo-lead", working ? "AI 正在处理" : "还有活没干"));\n'
# 1.25.21 起，下面五条锚点已经过期，不是这个脚本坏了，是那一版把它们做得更严了。
# 它们连同 1.25.21 新加的「整行删掉」变体，都搬到了 tools\mutation_check_todo_cost_split.py：
#   · 折叠条上那三样数字的措辞（现在要求折叠条和展开后的按钮用同一套说法）
#   · 第三样从「下面有几颗按钮」改成「还有多少题没答案」
#   · 筛选行那两列（现在是 auto auto，不再是 1fr + 命名区域）
# 留着这几个常量会让人以为它们还在被检查；删掉又看不出是迁移而不是遗忘 —— 所以留注释。
# 再加一条硬约束：这个脚本里不允许出现 RETIRED 里的名字，忘了搬会被当场抓住。
RETIRED_IN_1_25_21 = ("PENDING_BIT", "UNTAGGED_BIT", "AI_BIT", "AREAS", "AREAS_DEAD")
assert not any(name in globals() for name in RETIRED_IN_1_25_21), (
    "these anchors belong to 1.25.21 now — they are checked by "
    "tools/mutation_check_todo_cost_split.py")
BATCH_SELECTOR = 'document.querySelectorAll("#libraryTodos .library-tool-actions button")'
BATCH_SELECTOR_ALL = 'document.querySelectorAll("#libraryTodos button")'
TITLE_OFF = '题库里没有开启「AI 生成标签建议」这项功能，要到「设置」里打开才能用。'
TITLE_OFF_BAD = '现在不能使用。'
TITLE_COUNTED = '      ? `让 AI 给勾选的 ${count} 道题生成标签建议。生成的只是建议，要你逐条核对确认后才贴到题目上。每道题调用一次 AI，会消耗额度。`'
TITLE_UNCOUNTED = '      ? "让 AI 给勾选的题目生成标签建议。生成的只是建议，要你逐条核对确认后才贴到题目上。每道题调用一次 AI，会消耗额度。"'
READY_LINE = '    return state.features.knowledge_tags === true && state.ai.mode === "api" && state.ai.api_ready === true;'
READY_LOOSE = '    return state.features.knowledge_tags === true;'
# 「还没填密钥」和「填了没测通」是两种原因，原来一律写成「配置并测试通过」，
# 老师换过密钥后没重测，看见那句话只会以为功能坏了。这两条锚点盯住这个区别。
TITLE_NO_KEY = '      if (state.ai.key_configured !== true) return "还没填 API 密钥。到「设置 → API 配置」里填上密钥、选好模型，再点一次「测试连接」，这一项才能用。";'
TITLE_NO_KEY_FLAT = '      if (state.ai.key_configured !== true) return "需要先到「设置 → API 配置」里配置并测试通过，才能用这一项。";'
TITLE_KEY_READY = '      return "密钥填好了，但连接还没测过（换过密钥或改过模型都要重测）。到「设置 → API 配置」里点一次「测试连接」，这一项就能用。";'
TITLE_KEY_FLAT = '      return "需要先到「设置 → API 配置」里配置并测试通过，才能用这一项。";'
# 「没入库答案」原来取的是当前筛选下的数，再去减全库的数，两个口径混着减就减出负数。
ANSWER_TOTAL_LINE = '    const noAnswerTotal = Number(state.tagWork?.missing_answer_total) || 0;'
ANSWER_TOTAL_FILTERED = '    const noAnswerTotal = Number(state.facets?.answers?.no) || 0;'

# (relative, description, original, mutation)
MUTATIONS = [
    (LIBRARY_HTML,
     "the pending-work entry goes back to the filter row, next to 来源",
     BULK_TODOS, ""),

    (LIBRARY_HTML,
     "「批量生成标签建议」 goes back inside .bulk-actions, which is hidden until something is ticked — "
     "the markup and the sync are both still correct, the button is simply gone again",
     SUGGEST_SLOT,
     '          <div class="bulk-actions">\n'
     '            <button id="generateSelectedTags" class="button button-quiet button-small" type="button" disabled\n'
     '              title="先在左边勾几道题，再点这个让 AI 给它们生成标签建议">批量生成标签建议</button>\n'
     '          </div>'),

    (LIBRARY_HTML,
     "「批量生成标签建议」 is hidden in the markup again, so the first frame has no such button",
     SUGGEST, SUGGEST_HIDDEN),

    (LIBRARY_HTML,
     "「批量生成标签建议」 loses its hover note, leaving a grey button that says nothing",
     SUGGEST, SUGGEST_NO_TITLE),

    (LIBRARY_HTML,
     "the visible hint next to the grey button is dropped again: the reason only lives in a "
     "hover note nobody reads, so the button looks broken",
     SUGGEST_HINT, ''),

    (LIBRARY_JS,
     "each refresh hides the tag-suggestion button again",
     SYNC_SHOW, "    suggest.hidden = true;"),

    (LIBRARY_JS,
     "the cost warning takes over the entry's name again, so nobody can tell what it does",
     LEAD_LINE, LEAD_LINE + COST_BADGE),

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

    (LIBRARY_JS,
     "「还没填密钥」and「填了但没测通」are told apart no more, so a teacher who replaced the key "
     "is told to configure it again instead of to re-test the connection",
     TITLE_NO_KEY, TITLE_NO_KEY_FLAT),

    (LIBRARY_JS,
     "the case of a filled-in key that was never tested says the same thing as a missing key",
     TITLE_KEY_READY, TITLE_KEY_FLAT),

    (LIBRARY_JS,
     "the pending-work panel goes back to mixing scopes: the filtered count minus the "
     "whole-library count, which is where the negative number came from",
     ANSWER_TOTAL_LINE, ANSWER_TOTAL_FILTERED),
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

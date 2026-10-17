"""Mutation check for the batch-button truth and the press feedback.

Every mutation is reverted before the next one starts. Reverting only in the
final ``finally`` means mutation N is measured on top of mutation N-1, every
failure prints the same first assertion, and the report looks like a clean
sweep while most of it was never exercised at all.

Run with the packaging build Python:

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_batch_button_truth.py
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

# Windows 控制台默认 cp1252，而每条结果都要打印「挡住 ✓ / 漏过 ✗」。以前它在
# 第一条结果上就崩了，退出码变成 1 —— 于是「变异全被挡住」和「脚本自己坏了」
# 在外面看一模一样，这个门禁自己开始说谎。先把输出编码定死。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PYTHON = ROOT / "packaging" / ".build" / "venv" / "Scripts" / "python.exe"
NODE = "node"

# (file, description, original, mutation, command)
MUTATIONS = [
    ("backend/core/library_browse.py",
     "batch button counts every untagged question again, including ones already waiting",
     'return {"untagged_knowledge": untagged.distinct().count(),\n'
     '            "untagged_ready": untagged.exclude(pk__in=already_asked).distinct().count(),',
     'return {"untagged_knowledge": untagged.distinct().count(),\n'
     '            "untagged_ready": untagged.distinct().count(),',
     [PYTHON, "manage.py", "test", "core.test_library_batch_button_truth"]),

    ("backend/core/library_browse.py",
     "already-asked questions are counted as queueable again",
     '"untagged_ready": untagged.exclude(pk__in=already_asked).distinct().count(),',
     '"untagged_ready": untagged.distinct().count(),',
     [PYTHON, "manage.py", "test", "core.test_library_batch_button_truth"]),

    ("backend/core/library_browse.py",
     "the missing-answer count stops looking at already-generated AI answers",
     'missing_answers = sum(1 for content, extras in live.values_list("content", "extras")\n'
     '                          if not str((content or {}).get("answer") or "").strip()\n'
     '                          and not (extras or {}).get("ai_answer"))',
     'missing_answers = sum(1 for content, extras in live.values_list("content", "extras")\n'
     '                          if not str((content or {}).get("answer") or "").strip())',
     [PYTHON, "manage.py", "test", "core.test_library_batch_button_truth"]),

    ("backend/core/library_browse.py",
     "the panel stops reporting how many questions are still being processed",
     '"queued_tags": processing["tags"],\n            "queued_answers": processing["answers"],',
     '"queued_tags": 0,\n            "queued_answers": 0,',
     [PYTHON, "manage.py", "test", "core.test_library_batch_button_truth"]),

    ("backend/core/views.py",
     "the batch sweeps stale jobs once per question again",
     "                job = library_jobs.enqueue(publication, kind, expire=False, **options)",
     "                job = library_jobs.enqueue(publication, kind, **options)",
     [PYTHON, "manage.py", "test", "core.test_library_batch_button_truth"]),

    ("frontend/library.js",
     "the pressed button stops changing its own wording",
     "    if (!label) return;",
     "    if (label) return;",
     [NODE, "test_library_api_only.js"]),

    ("frontend/library.js",
     "the other batch buttons stay clickable while one is queueing",
     "control.disabled = queueBusy || selectedView || control.dataset.disabledInSelectedView === \"true\";",
     "control.disabled = selectedView || control.dataset.disabledInSelectedView === \"true\";",
     [NODE, "test_library_api_only.js"]),

    ("frontend/library.js",
     "the queueing line stops counting the seconds already waited",
     "const tail = spent ? `，已等 ${spent} 秒` : \"\";",
     "const tail = \"\";",
     [NODE, "test_library_api_only.js"]),

    ("frontend/library.js",
     "a failed queue leaves the button stuck on its queueing wording",
     "    if (label && control.dataset.queueOriginal) label.nodeValue = control.dataset.queueOriginal;",
     "    if (label && control.dataset.queueOriginal && false) label.nodeValue = control.dataset.queueOriginal;",
     [NODE, "test_library_api_only.js"]),

    ("frontend/library.js",
     "the whole-library tag button skips the confirmation and spends quota at once",
     'confirmBulkTags(readyCount, missingCount);',
     'queueJobs("tags", { missing: true }, readyCount);',
     [NODE, "test_library_ai_panel.js"]),

    ("frontend/library.js",
     "the batch entry stops saying that it costs quota",
     'node("span", "library-batch-cost", "要花额度")',
     'node("span", "library-batch-cost", "")',
     [NODE, "test_library_context_sidebar.js"]),

    ("frontend/library.js",
     "the batch entry stops naming the two things it actually fills in",
     'node("summary", "", "批量补标签和答案")',
     'node("summary", "", "批量处理题目")',
     [NODE, "test_library_context_sidebar.js"]),

    ("frontend/library-reading.css",
     "the type filters are allowed to wrap onto a second row again",
     ".library-page .library-facet > #typeFilters {\n"
     "    display: flex; flex-wrap: nowrap; min-width: 0; gap: 0 15px;",
     ".library-page .library-facet > #typeFilters {\n"
     "    display: flex; flex-wrap: wrap; min-width: 0; gap: 0 15px;",
     [NODE, "test_library_context_sidebar.js"]),

    ("frontend/library-reading.css",
     "the type facet is no longer placed in its own full-width row",
     ".library-page .library-filterbar > .library-facet { grid-area: types; min-width: 0; }",
     ".library-page .library-filterbar > .library-facet { min-width: 0; }",
     [NODE, "test_library_context_sidebar.js"]),

    ("frontend/library.css",
     "the batch entry goes back to being permanently loud, the most expensive button on the page",
     "  border: 1px solid var(--line-strong); border-radius: 8px; background: var(--surface);\n"
     "  color: var(--ink-2); font-size: 12.5px; font-weight: 400; white-space: nowrap; cursor: pointer;",
     "  border: 1px solid var(--accent); border-radius: 8px; background: var(--accent);\n"
     "  color: #fff; font-size: 12.5px; font-weight: 650; white-space: nowrap; cursor: pointer;",
     [NODE, "test_library_context_sidebar.js"]),

    ("frontend/library.css",
     "the batch entry stops lighting up while AI is actually working",
     ".library-batch-toolbar .library-batch-ai[data-working] > summary {",
     ".library-batch-toolbar .library-batch-ai[data-never] > summary {",
     [NODE, "test_library_context_sidebar.js"]),

    ("frontend/library-reading.css",
     "the type filter buttons go back to claiming an equal slice of the row each",
     "    flex: 0 1 auto; justify-content: flex-start; min-width: 0;",
     "    flex: 1 1 0; justify-content: center; min-width: 0;",
     [NODE, "test_library_context_sidebar.js"]),

    ("frontend/library.js",
     "the pending-work filters put their search icon back",
     "`AI 建议待核对 · ${pendingCount} 题`",
     "`AI 建议待核对 · ${pendingCount} 题`, \"search\"",
     [NODE, "test_library_context_sidebar.js"]),

    # 下面四条盯的是「排队反馈扛得住重画」。原来那几句「正在排队…」只写在
    # 点下去那一个节点上，登记途中界面一重画，按钮就变回原名却仍然点不动 ——
    # 而且只有排队那几秒才偶发，平时怎么测都测不出来。
    ("frontend/library.js",
     "a re-render mid-queue leaves a silently disabled button again",
     "      if (queueBusy && control.dataset.queueAction === queueAction) markQueueing(control);\n"
     "      else unmarkQueueing(control);",
     "      unmarkQueueing(control);",
     [NODE, "test_library_api_only.js", "test_library_ai_panel.js"]),

    ("frontend/library.js",
     "every batch button claims to be queueing, not just the pressed one",
     "      if (queueBusy && control.dataset.queueAction === queueAction) markQueueing(control);",
     "      if (queueBusy) markQueueing(control);",
     [NODE, "test_library_api_only.js", "test_library_ai_panel.js"]),

    ("frontend/library.js",
     "the batch buttons stop carrying a stable identity",
     '      current.dataset.queueAction = "tags-current";',
     '      current.dataset.queueAction = "tags";',
     [NODE, "test_library_ai_panel.js"]),

    ("frontend/library.js",
     "the queueing wording stops following the shared state",
     '    label.nodeValue = "正在排队…";',
     '    label.nodeValue = "处理中…";',
     [NODE, "test_library_api_only.js", "test_library_ai_panel.js"]),

    ("frontend/library.js",
     "the seconds ticker writes into a node the re-render already threw away",
     '      const stateLine = document.querySelector("#aiBatchTools .library-batch-state");',
     '      const stateLine = null;',
     [NODE, "test_library_api_only.js"]),

    ("frontend/library.js",
     "the rebuilt panel line forgets it is queueing",
     "      stateLine.textContent = queueBusy ? queueingText(queueCount, 0) : workingSummary(queuedTags, queuedAnswers);",
     "      stateLine.textContent = workingSummary(queuedTags, queuedAnswers);",
     [NODE, "test_library_ai_panel.js"]),

    # 下面四条盯的是「核对在哪做」。以前面板只给一个「去核对」的按钮，其余全在
    # 标签窗口里：看建议要点一次，改完名再采纳又要跳回来，而面板那块下面空着。
    ("frontend/library.js",
     "the panel hides the suggestions behind a way-in button again",
     "        const panel = tagSuggestionNode(item, { embedded: true });\n        if (panel) box.append(panel);",
     "        const panel = null;\n        if (panel) box.append(panel);",
     [NODE, "test_library_context_sidebar.js"]),

    ("frontend/library.js",
     "the tag window draws a second copy of the suggestion list",
     "    ai.append(aiTitle, go); box.append(ai);",
     "    ai.append(aiTitle, tagSuggestionNode(item, { embedded: true }), go); box.append(ai);",
     [NODE, "test_library_tag_panel.js"]),

    ("frontend/library.js",
     "the tag window points at the wrong place",
     '"去右边面板核对"',
     '"去标签窗口核对"',
     [NODE, "test_library_tag_panel.js"]),

    ("frontend/library.js",
     "the window claims accepting happens inside it again",
     '"已经贴上的和想加的，都在这个窗口里；要采纳 AI 的建议，在右边详情面板上做。"',
     '"已经贴上的、AI 建议的、想加的，都在这个窗口里。"',
     [NODE, "test_library_tag_panel.js"]),

    ("frontend/library.js",
     "the way back to the panel clears the redraw marker too late to matter",
     "    inspectorRenderedKey = \"\";\n    renderInspector(item);",
     "    renderInspector(item);\n    inspectorRenderedKey = \"\";",
     [NODE, "test_library_context_sidebar.js"]),
]


def run(command, cwd):
    return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")


def main():
    if not PYTHON.is_file():
        raise SystemExit("Build the packaging venv first: packaging\\build.ps1")
    results = []
    for relative, description, original, mutation, command in MUTATIONS:
        target = ROOT / relative
        source = target.read_text(encoding="utf-8")
        if original not in source:
            raise SystemExit(f"{relative}: anchor not found, the check has gone stale:\n{original}")
        baseline = run(command, BACKEND if command[0] == PYTHON else ROOT / "frontend")
        if baseline.returncode != 0:
            print(baseline.stdout[-3000:], baseline.stderr[-3000:])
            raise SystemExit(f"{relative}: the unmutated suite already fails, refusing to trust this run")
        target.write_text(source.replace(original, mutation, 1), encoding="utf-8")
        try:
            broken = run(command, BACKEND if command[0] == PYTHON else ROOT / "frontend")
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

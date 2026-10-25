"""Mutation check for the export buttons and the draft menu entries.

Two things came out of walking the parts of the print preview nobody had looked at
closely. Driving the paper into 「答案与解析」and then switching AI answers off makes
「导出 Word」 and 「导出 PDF」 go grey together with an empty title — 1.25.15 had
explained 「分别导出题目卷与答案卷」 and left the two buttons a user presses most. And
with no draft open, 「保存草稿」 and 「复制为新草稿」 issue the very same request, so the
second one is only shown once a draft is actually open.

Same rules as the other checks: revert before the next mutation, a missing anchor
stops the run instead of reporting a clean sweep, and everything goes through bytes
because this tree mixes LF and CRLF (library.js is CRLF, the new test is LF).

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_print_draft_entries.py
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
COMMAND = ["node", "test_print_draft_entries.js"]

LIBRARY_JS = "frontend/library.js"
LIBRARY_HTML = "frontend/library.html"

COPY_TAG = '<button id="saveDraftAs" class="button button-quiet" type="button" hidden>复制为新草稿</button>'
SAVE_TAG = ('<button id="saveDraft" class="button" type="button" '
            'title="把这次组卷存下来，下次从“草稿”里接着改">保存草稿</button>')

ANSWER_WHY = ('    const answerWhy = answerEmpty ? "输出内容选了「答案与解析」，可这次组卷的题没有答案或解析，'
              '先改选题目，或去「答案解析」里补上答案" : "";')
MISSING_WHY = ('    const missingWhy = !printState.items.length ? "先把未载入的选题处理掉，才能导出"\n'
               '      : printState.missing.length ? `这次有 ${printState.missing.length} 道题没载入，先处理掉才能导出` : "";')
LAYOUT_WHY = ('    const layoutWhy = printState.layoutError ? `这份卷子有图表排不下：${printState.layoutError}`\n'
              '      : printState.layoutPending ? "正在量这份卷子的版面，稍等一下"\n'
              '      : printState.tooWide ? "有图表高于一张 A4，换个排法再导出" : "";')
WORD_TITLE = '    $("exportWord").title = why || "下载可继续编辑的 Word 文档";'
PDF_TITLE = '    $("exportPdf").title = why || layoutWhy || "下载按预览分页的 A4 PDF";'
PRINT_TITLE = '    $("printButton").title = why || layoutWhy || "打开系统打印窗口，按预览里看到的分页印";'
DRAFT_VISIBILITY = "    copy.hidden = !state.draft;"
DRAFT_SAVE_TITLE = ('    save.title = title ? `保存这次改动，覆盖正在编辑的「${title}」`\n'
                    '      : "把这次组卷存下来，下次从「草稿」里接着改";')
DRAFT_COPY_TITLE = "    copy.title = `另存一份新草稿，正在编辑的「${title}」保持不动`;"
DIRTY_HOOK = "  function markDraftDirty() {\n    syncDraftButtons();"
SAVED_HOOK = " · 有选题需要处理\" : \"\"}`;\n        syncDraftButtons();"

# (relative, description, original, mutation)
MUTATIONS = [
    (LIBRARY_JS,
     "an answers-only paper with no answers greys the buttons without saying why",
     ANSWER_WHY,
     '    const answerWhy = "";'),

    (LIBRARY_JS,
     "an unloaded question is no longer counted in the reason",
     MISSING_WHY,
     '    const missingWhy = !printState.items.length ? "先把未载入的选题处理掉，才能导出" : "";'),

    (LIBRARY_JS,
     "a figure taller than A4 no longer explains itself on the PDF button",
     LAYOUT_WHY,
     '    const layoutWhy = "";'),

    (LIBRARY_JS,
     "Word and PDF share one hover text again",
     WORD_TITLE,
     '    $("exportWord").title = $("exportPdf").title || "导出";'),

    (LIBRARY_JS,
     "PDF hides the layout problem while the paper cannot be laid out",
     PDF_TITLE,
     '    $("exportPdf").title = why || "下载按预览分页的 A4 PDF";'),

    (LIBRARY_JS,
     "the print button hides the layout problem too",
     PRINT_TITLE,
     '    $("printButton").title = why || "打开系统打印窗口，按预览里看到的分页印";'),

    (LIBRARY_JS,
     "the copy-draft entry shows up again with no draft open — two buttons, one operation",
     DRAFT_VISIBILITY,
     "    copy.hidden = false;"),

    (LIBRARY_JS,
     "saving no longer says it overwrites the draft you have open",
     DRAFT_SAVE_TITLE,
     '    save.title = "把这次组卷存下来";'),

    (LIBRARY_JS,
     "copying no longer says the original stays untouched",
     DRAFT_COPY_TITLE,
     '    copy.title = "另存一份新草稿";'),

    (LIBRARY_JS,
     "deleting the draft you are editing leaves the copy entry on screen",
     DIRTY_HOOK,
     "  function markDraftDirty() {"),

    (LIBRARY_JS,
     "saving a draft leaves the copy entry hidden until the next render",
     SAVED_HOOK,
     " · 有选题需要处理\" : \"\"}`;"),

    (LIBRARY_HTML,
     "the copy entry shows on the very first frame again, before anything syncs it",
     COPY_TAG,
     '<button id="saveDraftAs" class="button button-quiet" type="button">复制为新草稿</button>'),

    (LIBRARY_HTML,
     "the save button has no hover text on the very first frame",
     SAVE_TAG,
     '<button id="saveDraft" class="button" type="button">保存草稿</button>'),
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

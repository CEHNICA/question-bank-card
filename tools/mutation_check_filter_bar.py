"""Mutation check for tidying the question-bank screen (1.25.17).

Two things the teacher pointed at on the real machine: the 「题目详情」 button at the
top right is worthless — it duplicates the 「收起」 inside the panel, and on a wide
window clicking any question card pops the panel straight back (setInspectorOpen(true)
is hard-wired into selectInspectorQuestion), so it cannot even stay closed. And the
controls above the first question had grown into three rows and 21 of them; type and
sort moved into 「更多筛选」 so a wide window shows one row, with a chip row appearing
only while something is filtered.

Same rules as the other checks: revert before the next mutation, a missing anchor
stops the run instead of reporting a clean sweep, and everything goes through bytes
because this tree mixes LF and CRLF (library.html and library.css are CRLF, the new
test is LF).

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_filter_bar.py
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
COMMAND = ["node", "test_library_filter_bar.js"]

LIBRARY_HTML = "frontend/library.html"
LIBRARY_CSS = "frontend/library.css"
LIBRARY_JS = "frontend/library.js"

TYPE_BLOCK = ('<div class="library-facet">\n'
              '                  <span class="library-facet-label" id="typeFacetLabel">题型</span>\n'
              '                  <div id="typeFilters" class="draft-filters" role="group" '
              'aria-labelledby="typeFacetLabel"></div>\n'
              '                </div>\n')
SORT_LABEL = '<label class="library-select"><span>排序</span><select id="sortSelect" aria-label="题目排序">'
BEFORE_ADV = '            <details id="advancedFilters" class="library-advanced">'
TOOLS = '<div id="tagQuickTools" class="library-extra-tools" role="group" aria-label="标签待办" hidden></div>'
HIDE_RULE = "@media (min-width: 980px) { .inspector-toggle { display: none; } }"
CLOSE_BTN = '<button id="closeInspector" class="button button-quiet button-small inspector-close" type="button">收起</button>'
CHIP_TYPE = '"type", state.type ? `题型：${QB.TYPE_NAMES[state.type] || state.type}` : ""'

# (relative, description, original, mutation)
MUTATIONS = [
    (LIBRARY_HTML,
     "the type buttons go back to the outer row, one of the three rows comes back",
     TYPE_BLOCK, ""),

    (LIBRARY_HTML,
     "sort goes back to the outer row",
     SORT_LABEL,
     TYPE_BLOCK + '<label class="library-select"><span>排序</span><select id="sourceSelect"'),

    (LIBRARY_HTML,
     "the todo entries get pulled into 更多筛选, where an all-empty panel hides them",
     BEFORE_ADV,
     '            ' + TOOLS + '\n' + BEFORE_ADV),

    (LIBRARY_CSS,
     "the redundant 题目详情 button stays on screen again",
     HIDE_RULE, "/* the button stays */"),

    (LIBRARY_CSS,
     "the button is hidden on every width, leaving narrow screens no way to open the panel",
     HIDE_RULE, ".inspector-toggle { display: none; }"),

    (LIBRARY_CSS,
     "the hiding is flipped to narrow screens, which are the ones that need it",
     HIDE_RULE, "@media (max-width: 979px) { .inspector-toggle { display: none; } }"),

    (LIBRARY_HTML,
     "the only remaining way to close the panel is removed as well",
     CLOSE_BTN,
     '<button id="closeInspector" class="button button-quiet button-small inspector-close" '
     'type="button" hidden>收起</button>'),

    (LIBRARY_JS,
     "an applied type filter no longer shows up as a chip under the toolbar",
     CHIP_TYPE,
     '"type", ""'),

    (LIBRARY_CSS,
     "the rules for the type row come back as dead code",
     HIDE_RULE,
     HIDE_RULE + "\n.library-filterbar > .library-facet { flex: 1 1 300px; }"),

    (LIBRARY_CSS,
     "narrow screens hide the labels of the two dropdowns inside 更多筛选 again",
     ".library-filterbar > .library-select > span { display: none; }",
     ".library-filterbar .library-select span { display: none; }"),
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

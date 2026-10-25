"""Mutation check for the 「更多筛选」 panel layout (1.25.18).

The panel was a flex-wrap pile: the last type chip sat flush against the word 「排序」,
sort's dropdown floated alone at the right of the next line, and nothing shared a
column. Measured heights were 30 / 61 / 35 / 105 / 61 / 96 — no rhythm at all. It is a
grid of titled cards now.

Same rules as the other checks: revert before the next mutation, a missing anchor
stops the run instead of reporting a clean sweep, and everything goes through bytes
because this tree mixes LF and CRLF (library.html and library.css are CRLF, the new
test is LF).

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_advanced_panel.py
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
COMMAND = ["node", "test_advanced_filters_panel.js"]

LIBRARY_HTML = "frontend/library.html"
LIBRARY_CSS = "frontend/library.css"

GRID = "  display: grid; grid-template-columns: repeat(auto-fit, minmax(292px, 1fr)); align-items: start; gap: 12px;\n"
FLEX = "  display: flex; align-items: flex-start; gap: 10px 14px;"
CARD = (
    ".library-advanced[open] > .library-filters > * {\n"
    "  display: flex; min-width: 0; flex-direction: column; gap: 7px; align-items: stretch;\n"
    "  padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface);\n"
    "}"
)
FULL_ROW = (
    ".library-advanced[open] > .library-filters > .library-facet,\n"
    ".library-advanced[open] > .library-filters > .tag-saved-filter-box { grid-column: 1 / -1; }"
)
FACET_COLUMN = (
    ".library-advanced[open] > .library-filters .library-facet,\n"
    ".library-advanced[open] > .library-filters .library-filter-group "
    "{ flex-direction: column; align-items: stretch; gap: 7px; }"
)
HIDE_EMPTY = ".library-advanced[open] > .library-filters > .library-filter-group:has([hidden]) { display: none; }"
TAG_SELECT = (
    ".library-advanced[open] > .library-filters #tagSelect { width: 100%; max-width: none; "
    "min-height: 104px; border-radius: 8px; }"
)
GROUP_HTML = (
    '<div class="library-filter-group">\r\n'
    '                  <span class="library-filter-group-label">谁审核的</span>\r\n'
    '                  <div id="reviewFilters" class="draft-filters" role="group" aria-label="谁审核的" hidden></div>\r\n'
    "                </div>"
)

# (relative, description, original, mutation)
MUTATIONS = [
    (LIBRARY_CSS, "the panel goes back to flex-wrap, and the columns stop lining up", GRID, FLEX),
    (
        LIBRARY_CSS,
        "the cards stop stacking title over control",
        CARD,
        ".library-advanced[open] > .library-filters > * { display: flex; min-width: 0; flex-direction: row; "
        "gap: 7px; align-items: stretch; }",
    ),
    (
        LIBRARY_CSS,
        "the cards lose their outline and blur back into the pile",
        CARD,
        ".library-advanced[open] > .library-filters > * { display: flex; min-width: 0; "
        "flex-direction: column; gap: 7px; align-items: stretch; }",
    ),
    (
        LIBRARY_CSS,
        "the widest filter no longer gets a row of its own",
        FULL_ROW,
        ".library-advanced[open] > .library-filters > .library-facet, "
        ".library-advanced[open] > .library-filters > .tag-saved-filter-box { }",
    ),
    (
        LIBRARY_CSS,
        "题型 sits label-and-chips on one line again while every other card is stacked",
        FACET_COLUMN,
        ".library-advanced[open] > .library-filters .library-facet, "
        ".library-advanced[open] > .library-filters .library-filter-group { }",
    ),
    (
        LIBRARY_CSS,
        "an empty dimension leaves a title-only card behind",
        HIDE_EMPTY,
        "/* nothing hides an empty dimension */",
    ),
    (
        LIBRARY_CSS,
        "the rule to hide an empty card loses a level of the selector and stops matching",
        HIDE_EMPTY,
        ".library-advanced[open] > .library-filter-group:has([hidden]) { display: none; }",
    ),
    (
        LIBRARY_CSS,
        "the tag picker goes back to a narrow native listbox inside a wide card",
        TAG_SELECT,
        ".library-advanced[open] > .library-filters #tagSelect { }",
    ),
    (
        LIBRARY_HTML,
        "谁审核的 loses its on-screen title again",
        GROUP_HTML,
        '<div class="library-filter-group">\r\n'
        '                  <div id="reviewFilters" class="draft-filters" role="group" aria-label="谁审核的" hidden></div>\r\n'
        "                </div>",
    ),
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
    return subprocess.run(
        COMMAND, cwd=str(FRONTEND), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


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
    print(
        f"\n{len(results) - len(survived)}/{len(results)} 条变异均被挡住"
        if not survived
        else f"\n{len(survived)} 条变异漏过：{survived}"
    )
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())

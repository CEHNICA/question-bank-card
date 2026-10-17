"""Mutation check for the exam basket's row shape.

With three real questions in the basket, the first row measured 57px while the
other two measured 40px. Two causes sat on the same line: the question number was
printed twice (bold row and again after the paper name), and that line could
break anywhere, so a long paper name left "· 第" behind and pushed "4 题" down.

The mutations below aim at what the reader gets — one line per row, the number
printed once, the full name still reachable by hover and by screen reader.

Same rules as the other checks: revert before the next mutation, a missing
anchor stops the run instead of reporting a clean sweep, and everything goes
through bytes because this tree mixes LF and CRLF (library.js is CRLF,
library.css is CRLF, the new test is LF).

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_basket_rows.py
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
COMMAND = ["node", "test_basket_list_rows.js"]

LIBRARY_JS = "frontend/library.js"
LIBRARY_CSS = "frontend/library.css"

LOCATOR = "      const locator = item ? sourceLocator(item) : `选题 ${index + 1}`;"
FULL_NAME = "      const name = item ? `${item.source_filename} · ${locator}` : locator;"
LINE_NODE = ('      const line = node("span", "basket-item-source", item ? item.source_filename : locator);')
LINE_TITLE = "      line.title = name;"
STRONG = ('      open.append(node("strong", "", item ? `${locator} · ${QB.TYPE_NAMES[item.question_type] || "其他"}` '
          ': locator), line);')
REMOVE_LABEL = "      remove.setAttribute(\"aria-label\", `移出${name}`);"
CSS_SOURCE = (".basket-item-open .basket-item-source { white-space: nowrap; overflow: hidden; "
              "text-overflow: ellipsis; }")
CSS_STRONG = ".basket-item-open strong { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }"
CSS_GENERAL = (".basket-item-open span { display: block; margin-top: 4px; color: var(--muted); "
               "font-size: 11.5px; overflow-wrap: anywhere; }")

# (relative, description, original, mutation)
MUTATIONS = [
    (LIBRARY_JS,
     "the number is printed twice again, once on each line",
     LINE_NODE,
     '      const line = node("span", "", item ? `${item.source_filename} · ${locator}` : locator);'),

    (LIBRARY_JS,
     "the row loses its dedicated hook for the paper-name line",
     LINE_NODE,
     '      const line = node("span", "", item ? item.source_filename : locator);'),

    (LIBRARY_JS,
     "the clipped name can no longer be read in full",
     LINE_TITLE,
     "      line.title = \"\";"),

    (LIBRARY_JS,
     "the hover and the remove button stop naming the exact question",
     FULL_NAME,
     "      const name = item ? item.source_filename : locator;"),

    (LIBRARY_JS,
     "the remove button drops the question from its accessible name",
     REMOVE_LABEL,
     '      remove.setAttribute("aria-label", "移出这道题");'),

    (LIBRARY_JS,
     "the bold line goes back to computing the locator a third time",
     STRONG,
     ('      open.append(node("strong", "", item ? `${sourceLocator(item)} · '
      '${QB.TYPE_NAMES[item.question_type] || "其他"}` : locator), line);')),

    (LIBRARY_JS,
     "the fallback row no longer says where the question sits",
     LOCATOR,
     "      const locator = item ? sourceLocator(item) : \"\";"),

    (LIBRARY_CSS,
     "the paper name wraps again and the rows go ragged",
     CSS_SOURCE,
     "/* no rule: the general span rule breaks it anywhere */"),

    (LIBRARY_CSS,
     "the name overflows its row instead of being clipped",
     CSS_SOURCE,
     ".basket-item-open .basket-item-source { white-space: nowrap; }"),

    (LIBRARY_CSS,
     "the bold line is left free to wrap",
     CSS_STRONG,
     "/* removed: only the paper name is pinned */"),

    (LIBRARY_CSS,
     "the warning line about a withdrawn question gets clipped as well",
     CSS_GENERAL,
     (".basket-item-open span { display: block; margin-top: 4px; color: var(--muted); "
      "font-size: 11.5px; white-space: nowrap; overflow: hidden; }")),
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

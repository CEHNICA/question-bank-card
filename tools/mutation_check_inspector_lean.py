"""Mutation check for the lean question-details panel and the once-per-paper label (1.25.19).

The right panel used to copy the card on its left: number, type, paper name, and a
collapsed 「查看当前题面」 that rendered the whole question a second time — the paper
name appeared 17 times on one screen. The card itself wrote the paper name on every
card too, even though one paper runs for dozens of cards in a row.

Same rules as the other checks: revert before the next mutation, a missing anchor
stops the run instead of reporting a clean sweep, and everything goes through bytes
because this tree mixes LF and CRLF.

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_inspector_lean.py
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
COMMAND = ["node", "--test", "test_library_inspector_lean.js", "test_advanced_filters_panel.js"]

LIBRARY_JS = "frontend/library.js"
LIBRARY_CSS = "frontend/library.css"

PANEL_ON = "    if (ui.inspectorToggle) ui.inspectorToggle.disabled = false;"
FACTS = '    inspectorFact(facts, "核对", item.review?.source === "ai"'
APPEND_INFO = "    content.append(info);"
HIDDEN_RULE = ('    if (label) label.hidden = Boolean(prevItem) && '
               "prevItem.source_filename === item.source_filename;")
LOOP_CALL = ("      syncSourceLabel(node, item, index > 0 ? shown[index - 1] : null);\n")
EXPAND_CALL = ("      syncSourceLabel(replacement, item,\n"
               "        previousCard ? state.catalog.get(previousCard.id.slice(2)) : null);\n")
TOGGLE_TITLE = "先在左边点一道题，这里才有它的标签和待核对建议"
SOURCE_SPAN = 'node("span", "library-source", item.source_filename)'
FACTS_RULE = (".question-inspector-facts { display: grid; gap: 0; margin: 0; "
              "border-top: 1px solid var(--line); }")
ACCENT = ".question-inspector-tags { border-left: 3px solid var(--accent); padding-left: 10px; }"
HIDE_CARD = ".library-advanced[open] > .library-filters > [hidden] { display: none; }"

# (relative, description, original, mutation)
MUTATIONS = [
    # ── 面板不许把题卡上的东西再抄一遍 ──
    (LIBRARY_JS,
     "the panel prints the paper name again above the tag editor",
     PANEL_ON,
     PANEL_ON + '\n    content.append(node("p", "question-inspector-source", item.source_filename || "试卷"));'),

    (LIBRARY_JS,
     "the panel brings back 当前选中 / 定位题卡",
     PANEL_ON,
     PANEL_ON + '\n    const back = node("button", "button button-small question-inspector-locate", "定位题卡");\n'
                '    content.append(back);'),

    (LIBRARY_JS,
     "「题目信息」里那行「来源」又回来了",
     FACTS,
     '    inspectorFact(facts, "来源", item.source_filename || "试卷");\n' + FACTS),

    (LIBRARY_JS,
     "「查看当前题面」又回来了",
     APPEND_INFO,
     '    content.append(node("summary", "", "查看当前题面"), info);'),

    # 删掉的样式留着，下一个人会以为面板上还有那块。
    (LIBRARY_CSS,
     "dead styles for the removed panel pieces come back",
     FACTS_RULE,
     ".question-inspector-target { display: flex; }\n" + FACTS_RULE),

    (LIBRARY_CSS,
     "the accent bar that marks 「current question」 is dropped along with the heading",
     ACCENT,
     ".question-inspector-tags { padding-left: 10px; }"),

    # 空题库时那两张空壳卡片又回来了。
    (LIBRARY_CSS,
     "a card that is marked hidden shows itself again, leaving two empty cards in a fresh library",
     HIDE_CARD,
     "/* nothing hides a self-hidden card */"),

    # ── 试卷名同一张卷只写一次 ──
    (LIBRARY_JS,
     "the comparison is flipped, so the first card of every run goes nameless",
     HIDDEN_RULE,
     "    if (label) label.hidden = Boolean(prevItem) && prevItem.source_filename !== item.source_filename;"),

    (LIBRARY_JS,
     "every card hides its paper name",
     HIDDEN_RULE,
     "    if (label) label.hidden = true;"),

    (LIBRARY_JS,
     "no card ever shows a paper name",
     HIDDEN_RULE,
     "    if (label) label.hidden = false;"),

    (LIBRARY_JS,
     "the label is removed from the DOM instead of hidden, so a reused card can never come back",
     HIDDEN_RULE,
     "    if (label) label.remove();"),

    (LIBRARY_JS,
     "the card no longer builds the label at all, leaving nothing to hide",
     SOURCE_SPAN,
     'node("span", "library-source-removed", item.source_filename)'),

    (LIBRARY_JS,
     "the render loop stops judging the order, so repeats come back on every filter change",
     LOOP_CALL, ""),

    (LIBRARY_JS,
     "the loop compares each card against itself, so every card after the first goes nameless",
     LOOP_CALL,
     "      syncSourceLabel(node, item, index > 0 ? item : null);\n"),

    (LIBRARY_JS,
     "expanding a question rebuilds the card without re-judging, so the name pops back every time",
     EXPAND_CALL, ""),

    # ── 文案不许承诺面板里已经没有的东西 ──
    (LIBRARY_JS,
     "the hover line promises a 来源 row the panel no longer has",
     TOGGLE_TITLE,
     "先在左边点一道题，这里才有它的来源、标签和待核对建议"),
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

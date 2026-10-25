"""Mutation check for the tag directory's state note.

Walking into the tag page showed 299 rows all reading "0 题" with nothing on
the page saying why. The measured cause: 807 published questions, two of them
tagged, 443 proposals waiting. The page now says that in one sentence and offers
the one step that changes it.

These mutations aim at what the reader gets - the sentence, the button's name,
the numbers that reach the page, and the exit actually landing on the review
filter - rather than at whether a string exists.

Same rules as the other checks: revert before the next mutation, a missing
anchor stops the run instead of reporting a clean sweep, and everything goes
through bytes because this tree mixes LF and CRLF (library.js is CRLF,
library-tag-manager.js is LF) and a write_text() would rewrite the wrong one.

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_tag_directory_note.py
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
COMMAND = ["node", "test_tag_directory_state_note.js"]

MANAGER = "frontend/library-tag-manager.js"
LIBRARY = "frontend/library.js"
SERVICE = "backend/core/library_tagging.py"

NOTE_TEXT = "          text: lead + `另有 ${pending} 道题有 AI 标签建议等你核对，采纳后直接贴上，不用花钱。`,"
NOTE_BUTTON = "          button: `回题库核对这 ${pending} 道题的 AI 标签建议`,"
NOTE_LEAD = "      const lead = `题库 ${published} 道题里只有 ${tagged} 道贴了标签，所以下面几乎都是「0 题」。`;"
NOTE_FIELDS = """      const pending = Number(data.questions_pending_review) || 0;
      const actionable = Number(data.questions_pending_actionable) || 0;"""
NOTE_UNREACHABLE = """        const unreachable = pending - actionable;"""
NOTE_TITLE = (
    """              ? `其中 ${actionable} 道现在就能采纳，另外 ${unreachable} 道对不上现在的标签库，需要重新生成。`"""
)

NOTE_GUARD = "      if (!published || published - tagged <= 0) return null;"
NOTE_EXHAUSTED = """        button: "回题库给题目贴标签","""
EXIT_BUTTON = (
    'const goReview = button("回题库核对 AI 标签建议", '
    '() => { close(); options.onReviewSuggestions?.(); }, "button button-small");'
)
RENDER_HIDE = "      usageNoteBox.hidden = !note;"
APPEND = "    usageNoteBox.append(usageNoteText, goReview);"
RESULTS = "    results.append(location, listHeading, usageNoteBox, bulk, list, mapHost);"
CSS_RULE = (
    ".tag-directory-usage-note { display: flex; flex-direction: column; align-items: flex-start; "
    "gap: 8px; padding: 11px 14px; background: #eff6f2; border-bottom: 1px solid #dce9e1; }"
)
CSS_HIDDEN = ".tag-directory-usage-note[hidden] { display: none; }"
SERVICE_COUNTS = """            "published_questions": PublishedQuestion.objects.filter(
                status=PublishedQuestion.Status.PUBLISHED).count(),
            "tagged_questions": tagged_questions,
            "questions_pending_review": tag_work["pending_review"],
            "questions_pending_actionable": tag_work["pending_actionable"]}"""
SERVICE_FETCH = "    from .library_browse import tag_work_counts"
BASKET_EMPTY = (
    '      : !state.basket.length ? "篮子是空的。在左边任意一道题的右下角点「加入试题篮」，就会出现在这里；'
    '跨试卷、跨筛选都保留。"'
)
LIBRARY_HANDLER = """      onReviewSuggestions: () => {
        state.tag = ""; state.tagAll = ""; state.tagNot = ""; state.tagAny = "";
        state.tagMode = ""; state.tagReview = true; state.untaggedKnowledge = false;
        syncUrl(); void load();
      }"""

# (relative, description, original, mutation)
MUTATIONS = [
    (
        MANAGER,
        "the sentence no longer counts the waiting questions",
        NOTE_TEXT,
        '          text: lead + "还有题有 AI 标签建议等你核对。",',
    ),
    (MANAGER, "the button stops naming what it acts on", NOTE_BUTTON, '          button: "去核对",'),
    (
        MANAGER,
        "the button counts proposals instead of questions, next to the library's question count",
        NOTE_BUTTON,
        "          button: `回题库核对这 ${pending} 条 AI 标签建议`,",
    ),
    (
        MANAGER,
        "the note counts proposal rows again",
        NOTE_FIELDS,
        "      const pending = Number(data.questions_pending_review) || 0;\n      const actionable = 0;",
    ),
    (
        MANAGER,
        "the note stops saying which questions can be accepted right now",
        NOTE_UNREACHABLE,
        "        const unreachable = 0;",
    ),
    (
        MANAGER,
        "the hover stops separating the unreachable ones",
        NOTE_TITLE,
        "              ? `另外 ${unreachable} 道对不上现在的标签库，需要重新生成。`",
    ),
    (
        MANAGER,
        "the note drops the part explaining what the counts are",
        NOTE_LEAD,
        '      const lead = "下面这些标签大部分还没贴到题上。";',
    ),
    (
        MANAGER,
        "the cost of reviewing is quietly added to a step that is free",
        NOTE_TEXT,
        "          text: lead + `另有 ${pending} 道题有 AI 标签建议等你核对。`,",
    ),
    (
        MANAGER,
        "a fully tagged library still claims nothing is tagged",
        NOTE_GUARD,
        "      if (!published) return null;",
    ),
    (MANAGER, "an empty bar is shown even when there is nothing to say", NOTE_GUARD, "      if (false) return null;"),
    (
        MANAGER,
        "with nothing left to review the button still offers to review",
        NOTE_EXHAUSTED,
        '        button: "回题库核对这 439 道题的 AI 标签建议",',
    ),
    (
        MANAGER,
        "the exit leaves the directory open behind it",
        EXIT_BUTTON,
        (
            'const goReview = button("回题库核对 AI 标签建议", '
            '() => { options.onReviewSuggestions?.(); }, "button button-small");'
        ),
    ),
    (
        MANAGER,
        "the note hides itself by setting opacity instead of hiding",
        RENDER_HIDE,
        '      usageNoteBox.style.opacity = note ? "1" : "0";',
    ),
    (MANAGER, "the text and the exit drift apart", APPEND, "    usageNoteBox.append(usageNoteText);"),
    (
        MANAGER,
        "the note moves away from the list of zeros it explains",
        RESULTS,
        "    results.append(location, listHeading, bulk, usageNoteBox, list, mapHost);",
    ),
    (
        "frontend/library-tag-manager.css",
        "the hidden bar keeps its layout and leaves a gap",
        CSS_HIDDEN,
        "/* nothing: hidden bars still reserve a row */",
    ),
    (
        "frontend/library-tag-manager.css",
        "the note loses the rule that puts the exit under the text",
        CSS_RULE,
        "/* no rule at all: the bar stacks into an unstyled block */",
    ),
    (
        SERVICE,
        "the page stops being told how many questions are waiting",
        SERVICE_COUNTS,
        """            "published_questions": PublishedQuestion.objects.filter(
                status=PublishedQuestion.Status.PUBLISHED).count(),
            "tagged_questions": tagged_questions}""",
    ),
    (
        SERVICE,
        "the directory counts the backlog on its own instead of sharing the library's number",
        SERVICE_FETCH,
        "    # counted here as well, which is how 443 turned up next to 439",
    ),
    (
        SERVICE,
        "the tagged count stops counting questions once each",
        "    tagged_questions = len({publication_id for ids in publications_by_tag.values() for publication_id in ids})",
        "    tagged_questions = sum(len(ids) for ids in publications_by_tag.values())",
    ),
    (
        LIBRARY,
        "the empty basket stops saying where to press",
        BASKET_EMPTY,
        '      : !state.basket.length ? "把需要的题加入这里，跨试卷、跨筛选保留。"',
    ),
    (
        LIBRARY,
        "the instruction names a button that does not exist on the cards",
        BASKET_EMPTY,
        '      : !state.basket.length ? "篮子是空的。在左边任意一道题的右下角点「加入选题」，就会出现在这里；跨试卷、跨筛选都保留。"',
    ),
    (
        LIBRARY,
        "arriving from the tag page no longer filters to pending proposals",
        LIBRARY_HANDLER,
        """      onReviewSuggestions: () => {
        state.tag = ""; state.tagAll = ""; state.tagNot = ""; state.tagAny = "";
        state.tagMode = ""; state.tagReview = false; state.untaggedKnowledge = false;
        syncUrl(); void load();
      }""",
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

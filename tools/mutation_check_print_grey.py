"""Mutation check for why a control in the print preview cannot be used.

Four controls grey out with nothing on screen saying why. Measured live:
「分别导出题目卷与答案卷」carried title='' while 「答案解析」next to it always had
one; 「答案与解析」inside 输出内容 was locked as a disabled option, which shows no
tooltip at all; 「本题留白」was locked while the hint below it talked about
「这题另起一页」— a different control; and 「更多排版」/「调整单题」were 20px tall
next to a 34px button.

Same rules as the other checks: revert before the next mutation, a missing anchor
stops the run instead of reporting a clean sweep, and everything goes through bytes
because this tree mixes LF and CRLF (library.js and library.css are CRLF, the new
test is LF).

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_print_grey.py
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
COMMAND = ["node", "test_print_grey_explanations.js"]

LIBRARY_JS = "frontend/library.js"
LIBRARY_CSS = "frontend/library.css"

SPLIT_TITLE = (
    '    split.title = !printState.items.length ? "先把未载入的选题处理掉，才能分别导出题目卷与答案卷"\n'
    "      : printState.missing.length ? `这次有 ${printState.missing.length} 道题没载入，先处理掉才能分别导出`\n"
    '      : !printState.availableAnswers ? "这次组卷的题还没有答案或解析，拆不出答案卷"\n'
    '      : "导出两份文件：一份只有题目，一份只有答案与解析";'
)
NO_ANSWER_TITLE = '      : !printState.availableAnswers ? "这次组卷的题还没有答案或解析，拆不出答案卷"\n'
STATUS_GUARD = "    if (items.length && !available) {"
STATUS_NAMES_OPTION = "和「输出内容」里的「答案与解析」"
STATUS_NAMES_SWITCH = "「附 AI 参考（未核对）」"
STATUS_NAMES_ESCAPE = "先在顶栏的「答案解析」里补上答案"
SPACE_TITLE = (
    '    $("printIndividualSpace").title = !item ? "先在上面选一道题，才能逐题调整"\n'
    '      : item.question_type !== "free_response" ? "只对解答题有用，这道题留白没有意义"\n'
    '      : $("printDocument").value === "answers" ? "输出内容选了「答案与解析」，卷上没有做题的空白"\n'
    '      : ($("printDocument").value === "combined" && $("printAnswerLayout").value === "inline") ? "答案紧跟在题目后面，不留做题的空白"\n'
    '      : "这一题在卷面上留多少空白给学生写过程";'
)
SPACE_DOCUMENT_BRANCHES = (
    '      : $("printDocument").value === "answers" ? "输出内容选了「答案与解析」，卷上没有做题的空白"\n'
    '      : ($("printDocument").value === "combined" && $("printAnswerLayout").value === "inline") ? "答案紧跟在题目后面，不留做题的空白"\n'
)
BREAK_TITLE = (
    '    $("printIndividualBreak").title = !item ? "先在上面选一道题，才能逐题调整"\n'
    '      : id === printState.firstQuestion ? "第一题本来就在第一页，不用再另起一页"\n'
    '      : "让这道题从新的一页开始印";'
)
CSS_RULE = (
    ".print-advanced > summary, .print-individual > summary { box-sizing: border-box; "
    "min-height: 30px; padding-block: 5px; line-height: 20px; cursor: pointer; "
    "font-size: 13px; font-weight: 600; }"
)

# (relative, description, original, mutation)
MUTATIONS = [
    (LIBRARY_JS, "the greyed split button goes back to saying nothing on hover", SPLIT_TITLE, '    split.title = "";'),
    (LIBRARY_JS, "having no answers still promises two files", NO_ANSWER_TITLE, ""),
    (LIBRARY_JS, "the status line never says which entries are blocked", STATUS_GUARD, "    if (false) {"),
    (
        LIBRARY_JS,
        "the status line keeps complaining even after the answers are there",
        STATUS_GUARD,
        "    if (items.length) {",
    ),
    (LIBRARY_JS, "the status line stops naming the locked 答案与解析 option", STATUS_NAMES_OPTION, "和"),
    (
        LIBRARY_JS,
        "the status line points at a vague 「that switch」 instead of naming it",
        STATUS_NAMES_SWITCH,
        "上面那个开关",
    ),
    (
        LIBRARY_JS,
        "with no AI answers available, the real way out is no longer named",
        STATUS_NAMES_ESCAPE,
        "就只能先出题目卷了",
    ),
    (
        LIBRARY_JS,
        "本题留白 goes back to having no hover text",
        SPACE_TITLE,
        '    $("printIndividualSpace").title = "";',
    ),
    (
        LIBRARY_JS,
        "本题留白 always blames the question type, even when the paper is answers-only",
        SPACE_DOCUMENT_BRANCHES,
        "",
    ),
    (
        LIBRARY_JS,
        "这题另起一页 goes back to having no hover text",
        BREAK_TITLE,
        '    $("printIndividualBreak").title = "";',
    ),
    (
        LIBRARY_CSS,
        "the two section bars shrink back to a 20px click target",
        CSS_RULE,
        ".print-advanced > summary, .print-individual > summary { cursor: pointer; font-size: 13px; font-weight: 600; }",
    ),
    (
        LIBRARY_CSS,
        "the section bars lose their disclosure triangle to a flex display",
        CSS_RULE,
        ".print-advanced > summary, .print-individual > summary { display: flex; align-items: center; min-height: 30px; "
        "cursor: pointer; font-size: 13px; font-weight: 600; }",
    ),
    (
        LIBRARY_CSS,
        "only 更多排版 gets the taller click target, 调整单题 stays 20px",
        CSS_RULE,
        ".print-advanced > summary { box-sizing: border-box; min-height: 30px; padding-block: 5px; line-height: 20px; "
        "cursor: pointer; font-size: 13px; font-weight: 600; }",
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

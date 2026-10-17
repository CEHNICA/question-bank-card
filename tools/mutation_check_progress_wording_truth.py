"""Mutation check for the two progress readouts that contradicted each other.

Walking the review page as a person turned up two findings, both measured on the
live app rather than read off the source:

1. A paper's name row says 「已入库 25」 and the 3px bar right under it answered
   「不用再看 25 · 还要看 0」 on hover. One number, two names a few pixels apart,
   and 不用再看 never appears anywhere else on screen — nobody can tell whether
   it means "no need to re-read the paper", "no need to check it" or "no need to
   enter it into the library".
2. 「4 页尚未自动切出题目，可从原卷补齐。」 sits right under the paper title while
   the green banner 236px below says 「全部 25 题已处理完。」 Both true, but a
   reader sees one line saying work is left and another saying it is finished.

So the mutations aim at the strings the reader actually gets, plus the wiring
that keeps them in one place. Same rules as the other checks: revert before the
next mutation, and a missing anchor stops the run instead of reporting a clean
sweep.

Line endings are read and written with newline="" on purpose: this tree is LF,
and a plain write_text() on Windows would rewrite every line as CRLF — a huge
fake diff that also re-arms the silent-slice failure in the source-slicing
tests.

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_progress_wording_truth.py
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
COMMAND = ["node", "test_processing_progress.js"]

BAR_TITLE = """    if (done || todo) {
      bar.title = todo
        ? `这条细线是这份卷子的进度：${done ? `绿色 ${done} 题已经处理完，` : ""}橙色 ${todo} 题还要看。`
        : `这条细线是这份卷子的进度：绿色 ${done} 题都处理完了，没有还要看的题。`;
    }"""

NOTE_BODY = """    return total
      ? `原卷另有 ${pages} 页没切出题目，不在上面这 ${total} 道题里。需要更多题时可从原卷补齐。`
      : `原卷有 ${pages} 页没切出题目。需要更多题时可从原卷补齐。`;"""

NOTE_GUARD = "    if (pages <= 0) return \"\";"
NOTE_CALL = """        const note = QBProgress.manualPagesNote(manualPages.length, c.all);
        if (note) statusText.append(el("span", "processing-detail", note));"""
METER_FIGURE = "    $(\"meterFigure\").title = `这份卷子的进度：${c.approved} / ${c.all} 题已入库`;"
EXPORT = "canContinueAiCut, manualPagesNote };"

# (relative, description, original, mutation)
MUTATIONS = [
    ("frontend/app.js",
     "the bar goes back to 不用再看, a second name for the number printed above it",
     BAR_TITLE,
     '    if (done || todo) bar.title = `不用再看 ${done} · 还要看 ${todo}`;'),

    ("frontend/app.js",
     "the bar hides the finished half whenever anything is still waiting",
     BAR_TITLE,
     """    if (done || todo) {
      bar.title = todo
        ? `这条细线是这份卷子的进度：橙色 ${todo} 题还要看。`
        : `这条细线是这份卷子的进度：绿色 ${done} 题都处理完了，没有还要看的题。`;
    }"""),

    ("frontend/app.js",
     "a fully finished paper claims some are still waiting",
     BAR_TITLE,
     """    if (done || todo) {
      bar.title = `这条细线是这份卷子的进度：绿色 ${done} 题已经处理完，橙色 ${todo} 题还要看。`;
    }"""),

    ("frontend/app.js",
     "the missing-pages line stops saying which questions it is unrelated to",
     NOTE_BODY,
     """    return total
      ? `原卷另有 ${pages} 页没切出题目。需要更多题时可从原卷补齐。`
      : `原卷有 ${pages} 页没切出题目。需要更多题时可从原卷补齐。`;"""),

    ("frontend/app.js",
     "the missing-pages line goes back to sounding like unfinished work",
     NOTE_BODY,
     """    return total
      ? `${pages} 页尚未自动切出题目，可从原卷补齐。`
      : `${pages} 页尚未自动切出题目，可从原卷补齐。`;"""),

    ("frontend/app.js",
     "with nothing cut yet the line still talks about these 0 questions",
     NOTE_BODY,
     """    return total
      ? `原卷另有 ${pages} 页没切出题目，不在上面这 ${total} 道题里。需要更多题时可从原卷补齐。`
      : `原卷另有 ${pages} 页没切出题目，不在上面这 0 道题里。需要更多题时可从原卷补齐。`;"""),

    ("frontend/app.js",
     "the line still shows up when there is no uncut page at all",
     NOTE_GUARD,
     '    if (pages < 0) return "";'),

    ("frontend/app.js",
     "the page stops going through manualPagesNote and says the old thing inline",
     NOTE_CALL,
     """        statusText.append(el("span", "processing-detail",
          `${manualPages.length} 页尚未自动切出题目，可从原卷补齐。`));"""),

    ("frontend/app.js",
     "the empty line is appended anyway and leaves a blank row",
     NOTE_CALL,
     """        const note = QBProgress.manualPagesNote(manualPages.length, c.all);
        statusText.append(el("span", "processing-detail", note));"""),

    ("frontend/app.js",
     "the progress figure hover goes back to 不用再看",
     METER_FIGURE,
     '    $("meterFigure").title = `${c.approved} / ${c.all} 题不用再看（已通过或已入库）`;'),

    ("frontend/app.js",
     "manualPagesNote stops being reachable, so tests can no longer call it",
     EXPORT,
     "canContinueAiCut };"),
]


def read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def run() -> subprocess.CompletedProcess:
    return subprocess.run(COMMAND, cwd=str(FRONTEND), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main() -> int:
    results = []
    for relative, description, original, mutation in MUTATIONS:
        target = ROOT / relative
        source = read_text(target)
        if original not in source:
            raise SystemExit(f"{relative}: anchor not found, the check has gone stale:\n{original}")
        baseline = run()
        if baseline.returncode != 0:
            print(baseline.stdout[-3000:], baseline.stderr[-3000:])
            raise SystemExit(f"{relative}: the unmutated suite already fails, refusing to trust this run")
        write_text(target, source.replace(original, mutation, 1))
        try:
            broken = run()
        finally:
            write_text(target, source)
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

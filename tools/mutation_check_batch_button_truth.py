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
     'if (label) label.nodeValue = "正在排队…";',
     'if (label && false) label.nodeValue = "正在排队…";',
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
     "if (label && trigger.isConnected) label.nodeValue = original;",
     "if (label && trigger.isConnected && false) label.nodeValue = original;",
     [NODE, "test_library_api_only.js"]),

    ("frontend/library.js",
     "the whole-library tag button skips the confirmation and spends quota at once",
     'confirmBulkTags(tags, readyCount, missingCount);',
     'queueJobs("tags", { missing: true }, tags, readyCount);',
     [NODE, "test_library_ai_panel.js"]),

    ("frontend/library.js",
     "the batch entry stops saying that it costs quota",
     'node("span", "library-batch-cost", "要花额度")',
     'node("span", "library-batch-cost", "")',
     [NODE, "test_library_context_sidebar.js"]),

    ("frontend/library-reading.css",
     "the type filters are allowed to wrap onto a second row again",
     "grid-area: types; display: flex; flex-wrap: nowrap; min-width: 0; gap: 0 10px;",
     "grid-area: types; display: flex; flex-wrap: wrap; min-width: 0; gap: 0 10px;",
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

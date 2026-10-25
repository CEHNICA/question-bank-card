"""Mutation check for the More Filters panel's stability (1.25.24).

The teacher: 「这个界面啊不够稳定啊，我选择了两三个标签之后，这个界面就变了，格局就变了。」
The screenshot shows every count at 0 and a 谁审核的 card that was not there a moment
earlier, pushing everything below it two rows down.

Two causes, and fixing only one of them leaves the bug alive:
  · the backend counts each dimension as "how many would be left if you switched to
    that dimension", so every number moves when the filter moves;
  · the UI kept an `emptied` fallback that forced cards visible whenever a filter
    emptied the list — including dimensions the library never had any data for.

So the fix is a second set of numbers: presence, computed over the whole library and
therefore immune to filtering. Which cards exist must be decided by that, never by
"how many are left right now".

This runner needs both suites, because the fact lives in the backend and the decision
lives in the frontend: the backend mutations run the Django test, the rest run node.

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_panel_stability.py
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
BACKEND = ROOT / "backend"
PYTHON = ROOT / "packaging" / ".build" / "venv" / "Scripts" / "python.exe"

NODE = ["node", "--test", "test_library_panel_stability.js", "test_library_todo_merge.js",
        "test_library_context_sidebar.js"]
DJANGO = [str(PYTHON), "manage.py", "test", "core.test_library_panel_stability"]

LIBRARY_JS = "frontend/library.js"
LIBRARY_BROWSE = "backend/core/library_browse.py"

NODE_RUNS = (FRONTEND, NODE)
DJANGO_RUNS = (BACKEND, DJANGO)

# ── 后端：这张「事实表」本身必须与筛选无关 ──
PRESENCE_ALL = "    papers = library_rows({})\n"
PRESENCE_FILTERED = '    papers = library_rows({"type": "single_choice"})\n'
REVIEW_COUNT = '        "review_ai": papers.filter(review_source="ai").count(),\n'
REVIEW_COUNT_ZERO = '        "review_ai": 0,\n'
ANSWER_COUNT = '        "answer_missing": papers.filter(missing).count(),\n'
PRESENCE_KEY = '        "presence": dimension_presence(),\n'
PRESENCE_KEY_GONE = ""

# ── 前端：三张卡片都改认 presence ──
STORE = "state.presence = body.presence || state.presence"
STORE_GONE = ""
STORE_PATCH = "if (body.presence) state.presence = body.presence;"
STORE_PATCH_GONE = ""
INITIAL = "presence: { review_ai: 0, answer_missing: 0, tags: 0 },"
INITIAL_GONE = ""
REVIEW_RULE = "box.hidden = !Number(state.presence?.review_ai) && !state.review;"
REVIEW_FILTERED = "box.hidden = !ai && !state.review;"
REVIEW_NO_ESCAPE = "box.hidden = !Number(state.presence?.review_ai);"
ANSWER_RULE = "box.hidden = !Number(state.presence?.answer_missing) && !state.answer;"
ANSWER_FILTERED = "box.hidden = !no && !state.answer;"
ANSWER_NO_ESCAPE = "box.hidden = !Number(state.presence?.answer_missing);"
TAG_RULE = "ui.tagBox.hidden = !(tags.length || Number(state.presence?.tags)"
TAG_RULE_NO_PRESENCE = "ui.tagBox.hidden = !(tags.length"
PLACEHOLDER = 'if (!tags.length) ui.tagSelect.append(new Option("当前筛选下没有可用的标签", ""));'
PLACEHOLDER_GONE = ""

# ── 筛到 0 时，手上的标签得显示名字，不是数据库编号 ──
# 装好 1.25.24 复验时拍到的：标签框里写「已选标签 · 531aebeb6」。那是内部编号。
NAME_LOOKUP = "    names = {str(pk): name for pk, name in LibraryTag.objects.filter(pk__in=ids).values_list(\"pk\", \"name\")}"
NAME_LOOKUP_IDS = "    names = {tag_id: tag_id for tag_id in ids}"
NAME_LOOKUP_DROP = ""
# 少了这个 UUID 兜底，按名字筛的老写法（`tag` 传进来的是「集合间的基本关系」这种中文）
# 会把整个列表页打成 500 —— 真出过：两条既有测试当场报 badly formed UUID。
UUID_GUARD = '                ids.append(str(uuid.UUID(str(value))))'
UUID_GUARD_RAW = '                ids.append(str(value))'
NAME_KEY = '        "selected_tag_names": selected_tag_names(request.GET),'
NAME_KEY_GONE = ""
NAME_STORE = "state.selectedTagNames = body.selected_tag_names || {};"
NAME_STORE_GONE = ""
NAME_PATCH = "if (body.selected_tag_names) state.selectedTagNames = body.selected_tag_names;"
NAME_PATCH_GONE = ""
NAME_OPTION = "new Option(known ? known : `标签已被删除 · ${id.slice(0, 8)}`, id, true, true)"
NAME_OPTION_RAW_ID = "new Option('已选标签 · ' + id.slice(0, 8), id, true, true)"
NAME_OPTION_HIDDEN = "new Option(known, id, true, true)"

# (relative, runner, description, original, mutation)
MUTATIONS = [
    (LIBRARY_BROWSE, DJANGO_RUNS,
     "presence is computed through the current filter, so filtering to nothing makes the "
     "cards disappear and reappear — the original bug, moved to the backend",
     PRESENCE_ALL, PRESENCE_FILTERED),

    (LIBRARY_BROWSE, DJANGO_RUNS,
     "who-reviewed presence is hard-coded to 0, so that card can never appear again",
     REVIEW_COUNT, REVIEW_COUNT_ZERO),

    (LIBRARY_BROWSE, DJANGO_RUNS,
     "the listing stops carrying presence at all, so the frontend has nothing stable to look at",
     PRESENCE_KEY, PRESENCE_KEY_GONE),

    (LIBRARY_JS, NODE_RUNS,
     "the frontend stops storing presence and falls back to the filtered counts",
     STORE, STORE_GONE),

    (LIBRARY_JS, NODE_RUNS,
     "a partial refresh no longer updates presence — the panel's shape freezes at whatever "
     "the first page happened to say",
     STORE_PATCH, STORE_PATCH_GONE),

    (LIBRARY_JS, NODE_RUNS,
     "「谁审核的」 goes back to 'are there any left right now' — the card vanishes when a "
     "filter matches nothing",
     REVIEW_RULE, REVIEW_FILTERED),

    (LIBRARY_JS, NODE_RUNS,
     "「谁审核的」 loses the escape for its own active filter, so filtering by review hides "
     "the control you just used",
     REVIEW_RULE, REVIEW_NO_ESCAPE),

    (LIBRARY_JS, NODE_RUNS,
     "「有没有答案」 goes back to 'are there any left right now'",
     ANSWER_RULE, ANSWER_FILTERED),

    (LIBRARY_JS, NODE_RUNS,
     "「有没有答案」 loses the escape for its own active filter",
     ANSWER_RULE, ANSWER_NO_ESCAPE),

    (LIBRARY_JS, NODE_RUNS,
     "「标签」 only counts the tags the current filter happens to match, so picking a few "
     "tags can make the card itself disappear",
     TAG_RULE, TAG_RULE_NO_PRESENCE),

    (LIBRARY_JS, NODE_RUNS,
     "the card stays but the empty picker says nothing — an empty box reads as broken",
     PLACEHOLDER, PLACEHOLDER_GONE),

    (LIBRARY_JS, NODE_RUNS,
     "presence starts undefined instead of a defined shape",
     INITIAL, INITIAL_GONE),

    (LIBRARY_BROWSE, DJANGO_RUNS,
     "selected_tag_names hands back the ids instead of the names, so the teacher sees "
     "「已选标签 · 531aebeb6」 — the internal id, not the tag they picked",
     NAME_LOOKUP, NAME_LOOKUP_IDS),

    (LIBRARY_BROWSE, DJANGO_RUNS,
     "the name lookup is dropped entirely",
     NAME_LOOKUP, NAME_LOOKUP_DROP),

    (LIBRARY_BROWSE, DJANGO_RUNS,
     "the UUID guard is dropped, so filtering by tag name (a legacy `tag` parameter that "
     "carries Chinese text, not an id) turns the whole listing into a 500",
     UUID_GUARD, UUID_GUARD_RAW),

    (LIBRARY_BROWSE, DJANGO_RUNS,
     "the listing stops carrying the selected tags' names, so the frontend has no way to "
     "label them",
     NAME_KEY, NAME_KEY_GONE),

    (LIBRARY_JS, NODE_RUNS,
     "the frontend stops storing the selected tags' names",
     NAME_STORE, NAME_STORE_GONE),

    (LIBRARY_JS, NODE_RUNS,
     "a partial refresh no longer updates the names — renaming a tag never shows up",
     NAME_PATCH, NAME_PATCH_GONE),

    (LIBRARY_JS, NODE_RUNS,
     "the picker falls back to showing the raw id again when a name is unavailable, instead "
     "of saying the tag was deleted",
     NAME_OPTION, NAME_OPTION_RAW_ID),

    (LIBRARY_JS, NODE_RUNS,
     "a selected tag whose name is unknown renders as an empty row, so the teacher cannot "
     "tell what is selected or cancel it",
     NAME_OPTION, NAME_OPTION_HIDDEN),
]


def apply(source: str, original: str, mutation: str) -> str:
    """Swap one anchor, matching \\n or \\r\\n so a mixed-ending tree still works."""
    if original in source:
        return source.replace(original, mutation, 1)
    crlf_anchor = original.replace("\n", "\r\n")
    if crlf_anchor in source:
        return source.replace(crlf_anchor, mutation.replace("\n", "\r\n"), 1)
    raise SystemExit(f"anchor not found, the check has gone stale:\n{original!r}")


def run(runner) -> subprocess.CompletedProcess:
    cwd, command = runner
    return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main() -> int:
    results = []
    for relative, runner, description, original, mutation in MUTATIONS:
        target = ROOT / relative
        raw = target.read_bytes()
        source = raw.decode("utf-8")
        try:
            mutated = apply(source, original, mutation)
        except SystemExit as error:
            raise SystemExit(f"{relative}: {error}") from None
        if mutated == source:
            raise SystemExit(f"{relative}: anchor not found, the check has gone stale:\n{original!r}")
        baseline = run(runner)
        if baseline.returncode != 0:
            print(baseline.stdout[-3000:], baseline.stderr[-3000:])
            raise SystemExit(f"{relative}: the unmutated suite already fails, refusing to trust this run")
        target.write_bytes(mutated.encode("utf-8"))
        try:
            broken = run(runner)
        finally:
            target.write_bytes(raw)
        if target.read_bytes() != raw:
            raise SystemExit(f"{relative}: restoring by bytes did not give the original file back")
        caught = broken.returncode != 0
        results.append((description, caught))
        print(("挡住 OK   " if caught else "漏过 FAIL ") + description)
        if not caught:
            print((broken.stdout or "")[-2000:] + (broken.stderr or "")[-2000:])
    survived = [item for item in results if not item[1]]
    print(f"\n{len(results) - len(survived)}/{len(results)} 条变异均被挡住" if not survived
          else f"\n{len(survived)} 条变异漏过：{survived}")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())

"""变异验证：标签层级这批新测试真的守得住，不是碰巧绿。

每个变异都故意把实现改坏，然后要求对应测试**必须失败**。
一个测试如果把坏实现也放过去，它就等于没测。

这里盯的是四件最容易悄悄退化的事：
  1. 章节取的是最上层祖先（跨学段的知识点要两处都能找到）；
  2. 目录不再下发分组；
  3. 概念父级覆盖不到的章节，要补一条直连的边；
  4. 模板导入要把章节建成父标签；
外加一条界面行为：选标签时按章节子树过滤。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAGGING = ROOT / "backend" / "core" / "library_tagging.py"
SEED = ROOT / "backend" / "core" / "tag_catalogue_seed.py"
LIBRARY = ROOT / "frontend" / "library.js"
VENV_PY = ROOT / "packaging" / ".build" / "venv" / "Scripts" / "python.exe"

ORIGINALS = {path: path.read_text(encoding="utf-8") for path in (TAGGING, SEED, LIBRARY)}

DIRECTORY_TEST = "core.test_library_tagging.FormalLibraryTaggingTests.test_the_catalogue_never_carries_groups_any_more"
CHAPTER_TEST = "core.test_knowledge_catalogue.KnowledgeCatalogueTests.test_the_reported_chapter_is_the_topmost_one"
CHAPTER_EDGE_TEST = "core.test_tag_hierarchy_template.HierarchyTemplateTests.test_a_point_taught_in_both_stages_reaches_both_chapters"
TEMPLATE_TEST = "core.test_library_tag_hierarchy.LibraryTagHierarchyTests.test_template_dependencies_are_imported_and_repeat_preserves_user_hierarchy"
NO_GROUPS_TEST = "core.test_library_tagging.FormalLibraryTaggingTests.test_the_catalogue_never_carries_groups_any_more"


def broken_chapter_walks_one_level_only(text: str) -> str:
    """只看直接上级，跨学段的知识点就只剩一章能找到了。"""
    return text.replace(
        "    return set().union(*(_chapter_names(parent, context, by_id, nested) for parent in parents))",
        "    return {parent.name for parent in parents}", 1)


def broken_catalogue_republishes_groups(text: str) -> str:
    """目录又把分组塞回响应里，前端就会以为还能按分组筛。"""
    return text.replace(
        '    return {"revision": state.revision, "tags": tags,',
        '    return {"revision": state.revision, "tags": tags, "groups": [],', 1)


def broken_blueprint_drops_chapter_edges(text: str) -> str:
    """只留概念父级，父级链路够不到的章节就没有入口了。"""
    return text.replace(
        '        result[key] = parents + [section for section in row["section_keys"] if section not in inherited]',
        "        result[key] = parents", 1)


def broken_template_skips_section_tags(text: str) -> str:
    """模板导入不建章节父标签，导入完还是一堆平铺标签。"""
    return text.replace(
        "            for key in keys:\n                spec = section_by_key[key]",
        "            for key in []:\n                spec = section_by_key[key]", 1)


def broken_subtree_is_just_itself(text: str) -> str:
    """按章节筛选退化成只看自己，章节下面一层都看不到。"""
    return text.replace("    return seen;\n  }\n  function tagTrail", "    return new Set([id]);\n  }\n  function tagTrail", 1)


DJANGO = [
    ("章节不再取最上层祖先", TAGGING, broken_chapter_walks_one_level_only, CHAPTER_TEST),
    ("目录又开始下发分组", TAGGING, broken_catalogue_republishes_groups, NO_GROUPS_TEST),
    ("章节边不再补挂", SEED, broken_blueprint_drops_chapter_edges, CHAPTER_EDGE_TEST),
    ("模板不再建章节父标签", TAGGING, broken_template_skips_section_tags, TEMPLATE_TEST),
]

NODE = [("选标签不再按子树过滤", LIBRARY, broken_subtree_is_just_itself)]


def run_django(label: str) -> tuple[int, str]:
    finished = subprocess.run([str(VENV_PY), "manage.py", "test", label], cwd=str(ROOT / "backend"),
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return finished.returncode, (finished.stdout or "") + (finished.stderr or "")


def run_node() -> tuple[int, str]:
    finished = subprocess.run(["node", "--test", str(ROOT / "frontend" / "test_tag_chapter_filtering.js")],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return finished.returncode, (finished.stdout or "") + (finished.stderr or "")


def restore() -> None:
    for path, original in ORIGINALS.items():
        path.write_text(original, encoding="utf-8")


def main() -> int:
    failures: list[str] = []

    for label, path, _, test in DJANGO:
        code, _ = run_django(test)
        if code != 0:
            print(f"[对照组] {label}：未改动实现时测试本应通过，实际失败")
            return 1
    print(f"[对照组] {len(DJANGO)} 条后台用例未改动实现时全部通过 ✓")
    code, output = run_node()
    if code != 0:
        print("[对照组] 前端用例未改动实现时本应通过，实际失败")
        print(output[-1500:])
        return 1
    print("[对照组] 前端用例未改动实现时通过 ✓")

    try:
        for label, path, breaker, test in DJANGO:
            mutated = breaker(ORIGINALS[path])
            if mutated == ORIGINALS[path]:
                print(f"[{label}] 变异没有真正改到代码，检查替换点")
                failures.append(label)
                continue
            path.write_text(mutated, encoding="utf-8")
            code, output = run_django(test)
            if code == 0:
                failures.append(label)
                print(f"[{label}] 坏实现竟然通过了测试 —— 这个测试没守住")
            else:
                first = next((line.strip() for line in output.splitlines()
                              if "Error" in line or "FAIL" in line), "")
                print(f"[{label}] 测试如期失败 ✓ {first[:90]}")
        for label, path, breaker in NODE:
            mutated = breaker(ORIGINALS[path])
            if mutated == ORIGINALS[path]:
                print(f"[{label}] 变异没有真正改到代码，检查替换点")
                failures.append(label)
                continue
            path.write_text(mutated, encoding="utf-8")
            code, output = run_node()
            if code == 0:
                failures.append(label)
                print(f"[{label}] 坏实现竟然通过了测试 —— 这个测试没守住")
            else:
                first = next((line.strip() for line in output.splitlines()
                              if "AssertionError" in line), "")
                print(f"[{label}] 测试如期失败 ✓ {first[:90]}")
    finally:
        restore()

    for path, original in ORIGINALS.items():
        if path.read_text(encoding="utf-8") != original:
            print(f"严重：{path.name} 没能还原成原样")
            return 1
    print("实现已还原")

    for label, _, _, test in DJANGO:
        code, _ = run_django(test)
        if code != 0:
            print(f"还原后用例没有恢复通过：{label}")
            return 1
    code, _ = run_node()
    if code != 0:
        print("还原后前端用例没有恢复通过")
        return 1
    print("还原后全部用例仍通过 ✓")

    if failures:
        print(f"\n{len(failures)} 个变异没被测试挡住：{failures}")
        return 1
    print("\n所有变异都如期被测试挡住 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

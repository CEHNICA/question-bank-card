"""变异验证：0029 的重新对齐真的按判据走，不是碰巧绿。

四个变异各自把实现改坏一点，然后要求对应测试**必须失败**。
一个测试如果把坏实现也放过去，它就等于没测。

这里盯的是这条链上最容易悄悄退化的地方：只认「标签还在」而不看「还在不在用」，
把坏掉的建议也救回来；或者干脆不救；或者救的时候顺手改了点别的。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
MIGRATION = BACKEND / "core" / "migrations" / "0029_realign_untouched_suggestions.py"
VENV_PY = ROOT / "packaging" / ".build" / "venv" / "Scripts" / "python.exe"

TESTS = "core.test_migration_realign_suggestions"

ORIGINAL = MIGRATION.read_text(encoding="utf-8")


def run_tests() -> tuple[int, str]:
    finished = subprocess.run(
        [str(VENV_PY), "manage.py", "test", TESTS],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return finished.returncode, (finished.stdout or "") + (finished.stderr or "")


def ignores_retirement(text: str) -> str:
    """只查标签「在不在」，不查「还在不在用」—— 停用的标签会被救回来。"""
    return text.replace(
        "alive = {str(tag.pk) for tag in LibraryTag.objects.all() if tag.active}",
        "alive = {str(tag.pk) for tag in LibraryTag.objects.all()}",
        1,
    )


def skips_the_intact_broken_split(text: str) -> str:
    """干脆不判断好坏了，把所有落后的待核对建议都抬上去。"""
    return text.replace(
        "        (broken if _split(apps, entries, alive) else intact).append(pk)", "        intact.append(pk)", 1
    )


def aligns_nothing(text: str) -> str:
    """什么都不做 —— 就是迁移没跑过的样子。"""
    return text.replace(
        "    if intact:\n        LibraryTagSuggestion.objects.filter(pk__in=intact).update(taxonomy_revision=revision)",
        "    return",
        1,
    )


def silently_advances_status(text: str) -> str:
    """对齐时顺手把状态改成已采纳 —— 用户会发现建议凭空消失了。"""
    return text.replace(
        ".update(taxonomy_revision=revision)", '.update(taxonomy_revision=revision, status="accepted")', 1
    )


MUTATIONS = [
    ("不区分标签是否还在用", ignores_retirement),
    ("不做好坏之分，一律对齐", skips_the_intact_broken_split),
    ("根本不对齐", aligns_nothing),
    ("对齐时顺手把状态改成已采纳", silently_advances_status),
]


def main() -> int:
    failures: list[str] = []

    code, output = run_tests()
    if code != 0:
        print("[对照组] 未改动实现时用例本应通过，实际失败")
        print(output[-1500:])
        return 1
    print("[对照组] 用例未改动实现时通过 ✓")

    try:
        for label, breaker in MUTATIONS:
            # 每个变异都必须从干净的实现重新开始。少了这一句，后面的变异会在
            # 前一个的坏状态上测，看上去「全被挡住」，其实一条都没验证到。
            MIGRATION.write_text(ORIGINAL, encoding="utf-8")
            mutated = breaker(ORIGINAL)
            if mutated == ORIGINAL:
                print(f"[{label}] 变异没有真正改到代码，检查替换点")
                failures.append(label)
                continue
            MIGRATION.write_text(mutated, encoding="utf-8")
            code, output = run_tests()
            if code == 0:
                failures.append(label)
                print(f"[{label}] 坏实现竟然通过了测试 —— 这个测试没守住")
            else:
                hit = next(
                    (line.strip() for line in output.splitlines() if "FAIL:" in line or "AssertionError" in line), ""
                )
                print(f"[{label}] 测试如期失败 ✓ {hit[:110]}")
    finally:
        MIGRATION.write_text(ORIGINAL, encoding="utf-8")

    if MIGRATION.read_text(encoding="utf-8") != ORIGINAL:
        print("严重：迁移文件没能还原成原样")
        return 1
    print("实现已还原")

    code, _ = run_tests()
    if code != 0:
        print("还原后用例没有恢复通过")
        return 1
    print("还原后用例仍通过 ✓")

    if failures:
        print(f"\n{len(failures)} 个变异没被测试挡住：{failures}")
        return 1
    print("\n所有变异都如期被测试挡住 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

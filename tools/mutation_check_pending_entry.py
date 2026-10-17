"""变异验证：待办入口这批新断言真的守得住，不是碰巧绿。

每个变异都故意把实现改坏，然后要求对应测试**必须失败**。
一个测试如果把坏实现也放过去，它就等于没测。

这条待办入口（待核对、未标知识点）在 1.25.0 里被塞在「更多筛选」浮层中，
而那个浮层在三组筛选都为空时会把整个折叠板一起隐藏 —— 于是「有 87 题待核对」
这种提示跟着一起从页面上消失，用户完全不知道还有活要干。
下面五个变异分别盯住这条链上的五个环节。

前四个是纯静态的（HTML/CSS/JS 文本），跑前端用例即可；
第五条是「按钮根本没渲染出来」，只能靠浏览器验收抓，这里只确认静态层不漏。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
HTML = FRONTEND / "library.html"
JS = FRONTEND / "library.js"
CSS = FRONTEND / "library.css"
READING_CSS = FRONTEND / "library-reading.css"

ORIGINALS = {path: path.read_text(encoding="utf-8") for path in (HTML, JS, CSS, READING_CSS)}

QUICK_MARKER = (
    '<div id="tagQuickTools" class="library-extra-tools" role="group" '
    'aria-label="标签待办" hidden></div>'
)


def run_node() -> tuple[int, str]:
    """前端用例的发现规则只认 *_test.js，本项目用的是 test_*.js，
    不显式传文件的话 node 会跑 0 个然后报告「全过」—— 那是个假绿灯。"""
    tests = sorted(str(path) for path in FRONTEND.glob("test_*.js"))
    if not tests:
        return 1, "找不到任何前端用例"
    finished = subprocess.run(
        ["node", "--test", *tests],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return finished.returncode, (finished.stdout or "") + (finished.stderr or "")


def parked_in_the_disclosure(text: str) -> str:
    """把待办入口挪回「更多筛选」浮层里 —— 退回 1.25.0 的老样子。"""
    if QUICK_MARKER not in text:
        return text
    anchor = '<div class="library-filters">'
    if anchor not in text:
        return text
    return text.replace(QUICK_MARKER, "", 1).replace(
        anchor, anchor + "\n                " + QUICK_MARKER, 1)


def hide_counts_the_pending_entry(text: str) -> str:
    """隐藏判断把待办入口也算进去 —— 它一有内容浮层就永远开着，或者反过来整个消失。"""
    return text.replace(
        '$("advancedFilters").hidden = [ui.answerFilters, $("reviewFilters"), ui.tagBox]',
        '$("advancedFilters").hidden = [ui.answerFilters, $("reviewFilters"), ui.tagBox, ui.extraTools]',
        1)


def loses_its_grid_slot(text: str) -> str:
    """元素不再被放进预留的格子 —— 会被自动排到第二行跟题型抢位置。"""
    return text.replace(" { grid-area: quick; display: flex;", " { display: flex;", 1)


def hidden_still_takes_room(text: str) -> str:
    """没活的时候也不消失 —— 筛选栏白白多出一个空格子。"""
    return text.replace(
        "  .library-page .library-filterbar > .library-extra-tools[hidden] { display: none; }\n",
        "", 1)


def buttons_never_rendered(text: str) -> str:
    """按钮压根没被塞进常驻容器 —— 页面上什么都没有。"""
    return text.replace(
        "box.append(...quickActions);",
        "box.dataset.ignored = String(quickActions.length);", 1)


MUTATIONS = [
    ("待办入口又退回「更多筛选」浮层里", HTML, parked_in_the_disclosure),
    ("隐藏判断又把待办入口算进去", JS, hide_counts_the_pending_entry),
    ("待办入口不再放进预留的格子", READING_CSS, loses_its_grid_slot),
    ("没活的时候入口也不消失", READING_CSS, hidden_still_takes_room),
    ("按钮根本没渲染到常驻容器", JS, buttons_never_rendered),
]


def restore() -> None:
    for path, original in ORIGINALS.items():
        path.write_text(original, encoding="utf-8")


def main() -> int:
    failures: list[str] = []

    code, output = run_node()
    if code != 0:
        print("[对照组] 未改动实现时前端用例本应通过，实际失败")
        print(output[-1500:])
        return 1
    print("[对照组] 前端用例未改动实现时通过 ✓")

    try:
        for label, path, breaker in MUTATIONS:
            # 每个变异都必须从干净的实现开始。少了这一句，第二个变异是在第一个的
            # 坏状态上测的 —— 后面的变异会一直挂在第一条断言上，看上去「全被挡住」，
            # 实际上一条都没验证到。守不住的那条会被前面一条的失败盖住。
            restore()
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
                hit = next(
                    (line.strip() for line in output.splitlines() if "AssertionError" in line),
                    "")
                print(f"[{label}] 测试如期失败 ✓ {hit[:110]}")
    finally:
        restore()

    for path, original in ORIGINALS.items():
        if path.read_text(encoding="utf-8") != original:
            print(f"严重：{path.name} 没能还原成原样")
            return 1
    print("实现已还原")

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

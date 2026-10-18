"""变异验证：题库页那三处（1.25.37）改回去要立刻被发现。

老师的原话：
  1「应该加上定位题目的按钮」
  2「回到上方的按钮，可以和审核界面的那个一致，鼠标移动过去他才会显示出来」
  3「搜索框太长」

三条都是「看一眼就知道对不对」的改动，最容易被后来的版本不知不觉改回去 ——
所以每一条都要有一道能报出来的闸。这脚本改一处、跑一次测试、
确认测试真的失败、再按字节还原。

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_locate_and_search.py
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
NODE = ["node", "--test", "test_library_locate_and_search.js"]

LIBRARY_JS = "frontend/library.js"
LIBRARY_HTML = "frontend/library.html"
LIBRARY_CSS = "frontend/library.css"

NODE_RUNS = (FRONTEND, NODE)

# ── 一、面板上的「定位题目」 ──
LOCATE_MOUNT = "      sectionTitle.append(locateQuestionButton(item));\n"
LOCATE_MOUNT_GONE = ""
LOCATE_LABEL = 'node("button", "button button-small inspector-locate", "定位题目")'
LOCATE_LABEL_EMPTY = 'node("button", "button button-small inspector-locate", "")'
LOCATE_SCROLL = 'card.scrollIntoView({ behavior: "smooth", block: "center" });'
LOCATE_SCROLL_TOP = 'card.scrollIntoView({ block: "nearest" });'
LOCATE_FLASH = 'card.classList.add("locate-flash");'
LOCATE_FLASH_GONE = ""
LOCATE_HIDE = "button.hidden = inView(document.getElementById(`q-${item.id}`));\n"
LOCATE_HIDE_GONE = ""
LOCATE_TIP = 'toast("这道题还没加载出来，滚到底或点「加载更多」再找。", "error");'
LOCATE_TIP_VAGUE = 'toast("找不到这道题。", "error");'
LOCATE_CSS = ".library-card.locate-flash { animation: library-locate-flash"
LOCATE_CSS_GONE = ".library-card.locate-removed { animation: none"

# ── 二、右下角「回到顶部」 ──
BACK_TO_TOP_TEXT = ">回到顶部</button>"
BACK_TO_TOP_ICON = '><svg class="icon" aria-hidden="true"><use href="#i-up"></use></svg></button>'
UP_SYMBOL = '<symbol id="i-up" viewBox="0 0 24 24">'
UP_SYMBOL_GONE = '<symbol id="i-up-removed" viewBox="0 0 24 24">'
SIZE_42 = "width: 42px;"
SIZE_FULL = "width: auto;"
# 锚点必须唯一。"border-radius: 50%" 整份 CSS 里有三处（开关滑块、回到顶部、还有别的），
# 用光这一串去 replace(..., 1) 会换到开关滑块那处，测试当然不报 —— 这是等价变换，不是有效变异。
# 所以连着前面那句一起锚，锁死「就是这一条」。
ROUND_50 = "place-items: center; width: 42px; height: 42px; padding: 0; border-radius: 50%;"
ROUND_PILL = "place-items: center; width: 42px; height: 42px; padding: 0; border-radius: 999px;"
FADE = "opacity: .3;"
FADE_HIDDEN = "opacity: 0;"
FADE_NONE = "opacity: 1;"
HOVER = ".back-to-top:hover, .back-to-top:focus-visible { opacity: 1"
HOVER_MOUSE_ONLY = ".back-to-top:hover { opacity: 1"

# ── 三、搜索框别再独吞整条顶栏 ──
# 注意锚点在 library-reading.css，不在 library.css。
# 本版真踩过：上限先写在 library.css，静态断言全绿，实机一量还是 1077 —— 因为
# library-reading.css 后加载，里面一条更具体的 `max-width: none` 把它整个盖掉了。
# 教训：决定渲染的是级联结果，锚必须落在**真正生效的那条**上。
READING_CSS = "frontend/library-reading.css"
SEARCH_MAX = ".library-top-navigation .library-search { flex: 0 1 560px; min-width: 140px; max-width: 560px; }"
SEARCH_MAX_NONE = ".library-top-navigation .library-search { flex: 1 1 260px; min-width: 140px; max-width: none; }"
SEARCH_FILL = ".library-search input { width: 100%;"
SEARCH_FILL_GONE = ".library-search input { width: 100% !important; min-width: 0;"


MUTATIONS = [
    # 「定位题目」没了
    (LIBRARY_JS, NODE_RUNS,
     "the panel no longer mounts the locate button, so the teacher's first ask is undone",
     LOCATE_MOUNT, LOCATE_MOUNT_GONE),
    (LIBRARY_JS, NODE_RUNS,
     "the locate button has no label — an icon-less button that says nothing",
     LOCATE_LABEL, LOCATE_LABEL_EMPTY),
    (LIBRARY_JS, NODE_RUNS,
     "locating scrolls the card to the nearest edge instead of the middle, so the question can "
     "land off-screen and you cannot tell where it went",
     LOCATE_SCROLL, LOCATE_SCROLL_TOP),
    (LIBRARY_JS, NODE_RUNS,
     "the located question no longer flashes — it scrolls into place and you lose track of it",
     LOCATE_FLASH, LOCATE_FLASH_GONE),
    (LIBRARY_CSS, NODE_RUNS,
     "the flash has no styling left, so adding the class does nothing",
     LOCATE_CSS, LOCATE_CSS_GONE),
    (LIBRARY_JS, NODE_RUNS,
     "the locate button shows even when the question is already on screen",
     LOCATE_HIDE, LOCATE_HIDE_GONE),
    (LIBRARY_JS, NODE_RUNS,
     "an unloaded question now just says 「找不到」, with no hint that it loads further down",
     LOCATE_TIP, LOCATE_TIP_VAGUE),

    # 「回到顶部」回到老样子
    (LIBRARY_HTML, NODE_RUNS,
     "the back-to-top button goes back to a text pill, so it no longer matches the review page",
     BACK_TO_TOP_ICON, BACK_TO_TOP_TEXT),
    (LIBRARY_HTML, NODE_RUNS,
     "the up-arrow symbol is dropped from the library page — the icon button renders empty",
     UP_SYMBOL, UP_SYMBOL_GONE),
    (LIBRARY_CSS, NODE_RUNS,
     "the button is no longer 42px, so the two pages no longer look the same",
     SIZE_42, SIZE_FULL),
    (LIBRARY_CSS, NODE_RUNS,
     "the button goes back to a pill shape",
     ROUND_50, ROUND_PILL),
    (LIBRARY_CSS, NODE_RUNS,
     "the button is fully invisible instead of faded — on a touch screen it can never be found",
     FADE, FADE_HIDDEN),
    (LIBRARY_CSS, NODE_RUNS,
     "the button is fully opaque by default, undoing 「鼠标移过去才显示」",
     FADE, FADE_NONE),
    (LIBRARY_CSS, NODE_RUNS,
     "only the mouse reveals it now — keyboard users can focus an invisible button",
     HOVER, HOVER_MOUSE_ONLY),

    # 搜索框回到独吞整条顶栏
    (READING_CSS, NODE_RUNS,
     "the search box goes back to filling the whole topbar (1077px on a 1520px screen)",
     SEARCH_MAX, SEARCH_MAX_NONE),
    (LIBRARY_CSS, NODE_RUNS,
     "the search box stops filling the available width on narrow screens",
     SEARCH_FILL, SEARCH_FILL_GONE),
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

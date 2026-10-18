"""变异验证：退出这条路的四道闸（1.25.38）。

老师的原话：「点击缩小点击退出按钮，有时候会卡住，甚至白屏，怎么回事」。

查出来是这样：
  * 一个题有据窗口背后挂着 8 个 Edge 进程 —— 1 个管窗口的主进程，加崩溃收集器、
    显卡进程、3 个工具进程、2 个页面进程。崩溃收集器是 Edge 故意留着的，窗口关了
    它还要把崩溃记录写完才走。
  * 退出时等的是「这个配置目录下还剩几个进程」，等的是它们，不是用户等的那扇窗。
    碰上残留就是永远等下去：服务不杀、运行记录不清，下次双击撞上半死的服务，白屏。
  * 本机实测：172 次启动只有 106 次干净收尾。
  * 查一次进程要 350–430 毫秒（要起一个 PowerShell），而退出是每 4 秒问一次。

这些改动最容易被后来的版本不知不觉改回去，所以每条都要有一道能报出来的闸：
改一处、跑一次测试、确认测试真的失败、再按字节还原。

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_quit_path.py
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
PYTHON = str(ROOT / "packaging" / ".build" / "venv" / "Scripts" / "python.exe")
WINDOW_TESTS = (str(ROOT), [PYTHON, "-m", "unittest", "test_app_window"])
LAUNCHER_TESTS = (str(ROOT), [PYTHON, "-m", "unittest", "test_launcher"])

APP_WINDOW = "app_window.py"
LAUNCHER = "start_question_bank.py"

# ── 锚点 ──
# 每个锚点都必须在它所在的源文件里只出现一次，否则 replace(..., 1) 换到的是别处，
# 测试当然全绿 —— 那不是有效变异，是等价变换。写完先数过：
#   app_window.py 里下面五处各 1 次；start_question_bank.py 里三处各 1 次。
#   （_WINDOW_LOOKUP_BACKOFF 这个名字整份文件出现 4 次，所以拿带括号的那一行当锚。）

# 「关好了」只看窗口，不看后台进程
ALIVE_IS_HANDLE = "return bool(hwnd) and bool(user32.IsWindow(hwnd))"
ALIVE_ALWAYS = "return bool(hwnd) and True"
# 「while _window_alive(user32, hwnd):」整份文件出现两次（主退出循环、重开的陪窗
# 循环），光换第一处只会换到陪窗那个，主退出循环一点没变 —— 等于什么都没验。
# 所以带上下一行把它锁死：主退出循环里跟的是服务存活检查，陪窗循环里没有。
WAIT_LOOP = ("while _window_alive(user32, hwnd):\n"
             "        if web.poll() is not None:")
WAIT_LOOP_FOREVER = ("while _window_alive(user32, hwnd) or True:\n"
                     "        if web.poll() is not None:")
# 窗口没出现时的重查要退避，别每 100ms 起一个 PowerShell
LOOKUP_BACKOFF = "next_lookup = time.monotonic() + ("
LOOKUP_NO_BACKOFF = "next_lookup = 0.0 + ("
# 兜底：拿不到句柄时到点就收手，不无限等
BACKSTOP = '        if time.monotonic() > deadline:\n            print("浏览器进程'
BACKSTOP_REMOVED = '        if False:\n            print("浏览器进程'
# 退出时把没人再听的那条控制请求删掉
CLEAR_CONTROL = "        control_file().unlink(missing_ok=True)"
CLEAR_CONTROL_SKIPPED = "        pass"
# 重开时先核对登着的那个进程还在不在
PID_GUARD = 'and _process_alive(record.get("pid"))'
PID_GUARD_REMOVED = "and True"
# 服务正在收尾时再宽限地试一次，别急着起第二份抢同一个数据库
SECOND_CHANCE = "_answer_is_ours(url) or _answer_is_ours(url, timeout=3.0)"
SECOND_CHANCE_REMOVED = "_answer_is_ours(url) or False"
# 问不到就当还活着，但真的退出了就要说不在
STILL_ACTIVE = "return code.value == 259"
STILL_ACTIVE_ALWAYS = "return True"

# ── 认窗口：按标题，不按面积 ──
# 装上去实测才发现的：上一次运行没退干净时，进程组里会多出一个比题有据窗口还大的
# 临时窗口。按面积取会挑中它，它随后自己消失，程序就以为「用户关了窗」，当场停
# 服务退出去 —— 真正开着、又连着死服务的题有据窗口被留在屏幕上。这就是白屏。
TITLE_MATCH = "if APP_TITLE in _window_title(user32, handle):"
TITLE_NEVER = "if False:"
PREFER_NAMED = "    if named:\n        return max(named)[1]"
PREFER_NAMED_OFF = "    if False:\n        return max(named)[1]"
STRICT_NO_WINDOW = "    if require_title:\n        return 0"
STRICT_OFF = "    if False:\n        return 0"


MUTATIONS = [
    # 退出卡住的根因：判据退回「还剩几个后台进程」
    (APP_WINDOW, WINDOW_TESTS,
     "「关好了」不再只看窗口句柄，于是又要去数后台进程 —— 崩溃收集器还没退干净就又开始等了",
     ALIVE_IS_HANDLE, ALIVE_ALWAYS),
    (APP_WINDOW, WINDOW_TESTS,
     "等待窗口消失的循环变成死等，窗口一关也停不下来",
     WAIT_LOOP, WAIT_LOOP_FOREVER),

    # 窗口赖着不走时的兜底
    (APP_WINDOW, WINDOW_TESTS,
     "拿不到窗口句柄时不再有上限，浏览器留一个进程就能让退出永远停在这一行",
     BACKSTOP, BACKSTOP_REMOVED),

    # 守护线程的资源争抢
    (APP_WINDOW, WINDOW_TESTS,
     "窗口还没出现时每一轮都重新定位，守护线程被自己起的 PowerShell 拖垮",
     LOOKUP_BACKOFF, LOOKUP_NO_BACKOFF),

    # 退出收尾
    (APP_WINDOW, WINDOW_TESTS,
     "退出时不再清掉那条没人再听的控制请求",
     CLEAR_CONTROL, CLEAR_CONTROL_SKIPPED),

    # 白屏那一侧：重开时会不会连到不相干或半死的服务
    (LAUNCHER, LAUNCHER_TESTS,
     "重开时不再核对登着的那个进程还在不在，于是连到退出卡住留下的半死服务",
     PID_GUARD, PID_GUARD_REMOVED),
    (LAUNCHER, LAUNCHER_TESTS,
     "服务正在收尾时不再宽限地重试，直接当成没在运行 —— 两份服务抢同一个数据库",
     SECOND_CHANCE, SECOND_CHANCE_REMOVED),
    (LAUNCHER, LAUNCHER_TESTS,
     "进程明明已经退出，却一律说它还活着，于是永远连着一个死端口",
     STILL_ACTIVE, STILL_ACTIVE_ALWAYS),

    # 认错窗口 → 以为用户关了窗 → 白屏
    (APP_WINDOW, WINDOW_TESTS,
     "认窗口又退回只看面积，于是挑中那个比题有据窗口还大的空白临时窗",
     TITLE_MATCH, TITLE_NEVER),
    (APP_WINDOW, WINDOW_TESTS,
     "即使标题对得上，也不优先用题有据窗口 —— 而是用那个空白临时窗",
     PREFER_NAMED, PREFER_NAMED_OFF),
    (APP_WINDOW, WINDOW_TESTS,
     "等着应用窗口出现时不再只认标题，于是第一把就抓一个临时窗口回来",
     STRICT_NO_WINDOW, STRICT_OFF),
]


def apply(source: str, original: str, mutation: str) -> str:
    """Swap one anchor, matching \\n or \\r\\n so a mixed-ending tree still works."""
    if original in source:
        return source.replace(original, mutation, 1)
    crlf_anchor = original.replace("\n", "\r\n")
    if crlf_anchor in source:
        return source.replace(crlf_anchor, mutation.replace("\n", "\r\n"), 1)
    raise SystemExit(f"anchor not found, the check has gone stale:\n{original!r}")


def run(runner, timeout: float = 90.0) -> subprocess.CompletedProcess:
    """跑一遍测试，逾时的当成「没通过」。

    这一版特意把一条变异写成了「等窗口的循环变成死等」。它抓住的方式不是报错，
    而是让测试永远跑不完：此时进程照样该算失败，否则这个脚本自己会被挂在原地，
    前面几条的结果一并丢掉。
    """
    cwd, command = runner
    try:
        return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command, 1, "", "测试挂死：这条变异让循环再也出不来（也算被挡住）")


def run_baseline(runner) -> subprocess.CompletedProcess:
    """基线不能挂死：基线挂了就说明测试套件本身有问题，这一轮全都不可信。"""
    result = run(runner)
    if "挂死" in (result.stderr or ""):
        raise SystemExit("the unmutated suite hangs, refusing to trust this run")
    return result


def check_nothing_left_behind() -> None:
    """开工前先确认源文件没停在某条变异上。

    这一版吃过一次亏：脚本跑到一半被总时限杀掉，写进去的变异没机会还原，源文件
    就停在「假装窗口永远活着」那一行。接着跑出来的测试结果全不可信，而界面看着
    只是「有点慢」——所以这里把它变成一道开工前的闸：任何一条锚点对不上或不唯一，
    立刻停，不往下走。
    """
    problems = []
    for relative, _runner, description, original, _mutation in MUTATIONS:
        source = (ROOT / relative).read_text(encoding="utf-8")
        count = source.count(original)
        if count != 1:
            problems.append(f"  {relative}: 锚点出现 {count} 次（应为 1 次）—— {description}")
    if problems:
        raise SystemExit(
            "锚点对不上，很可能上一次这个脚本被中途杀掉、变异没还原。\n"
            "先 git diff 看清楚源文件现在是什么样，修好再跑。\n" + "\n".join(problems))


def main() -> int:
    check_nothing_left_behind()
    results = []
    for relative, runner, description, original, mutation in MUTATIONS:
        target = ROOT / relative
        raw = target.read_bytes()
        source = raw.decode("utf-8")
        # 锚点必须唯一。出现两次以上时 replace(..., 1) 换到的是别处，等于什么都没验。
        count = source.count(original)
        if count != 1:
            raise SystemExit(
                f"{relative}: anchor appears {count} times, it must be unique:\n{original!r}")
        try:
            mutated = apply(source, original, mutation)
        except SystemExit as error:
            raise SystemExit(f"{relative}: {error}") from None
        if mutated == source:
            raise SystemExit(f"{relative}: anchor not found, the check has gone stale:\n{original!r}")
        baseline = run_baseline(runner)
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

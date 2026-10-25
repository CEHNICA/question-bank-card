"""变异验证：确认新测试真的守得住，不是碰巧绿。

每个变异都故意把实现改坏，然后要求测试**必须失败**。
一个测试如果把坏实现也放过去，它就等于没测。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "frontend" / "library.js"
TEST = ROOT / "frontend" / "test_job_failure_wording.js"

ORIGINAL = LIBRARY.read_text(encoding="utf-8")

READY_BRANCH = """    if (configurationIssue && ready) {
      return {
        head: `${label}上次没跑成`,"""

BROKEN_SELF_EXPLANATION = """    if (false && ready) {
      return {
        head: `${label}上次没跑成`,"""

# 变异一：不再区分「配好了」和「没配好」，一律原样复述历史原因。
# 这正是原来的毛病——配好之后仍然摆着「尚未配置」。
BROKEN_IGNORES_CURRENT_STATE = ORIGINAL.replace("if (configurationIssue && ready) {", "if (false) {", 1)

# 变异二：把「当前能不能用」这个判定拿掉，永远当成已经就绪。
# 于是还没配服务时也会说「现在已经配好了」。
BROKEN_ALWAYS_READY = ORIGINAL.replace("if (configurationIssue && ready) {", "if (configurationIssue) {", 1)

# 变异三：判定写死了不再跟着服务状态走（等于回到按钮与提示各写一份的老毛病）。
BROKEN_READY_IS_STATIC = ORIGINAL.replace(
    'if (state.ai.mode === "assistant") return kind === "tags" && state.ai.ready === true;', "return true;", 1
)

# 变异四：按钮不再复用统一判定，自己写一份。
BROKEN_JOBS_SPLIT_JUDGEMENT = ORIGINAL.replace(
    'const tagReady = aiReady("tags");\n    const answerReady = aiReady("answer");',
    'const tagReady = state.ai.mode === "api" && state.ai.api_ready === true;\n'
    '    const answerReady = state.ai.mode === "api" && state.ai.api_ready === true;',
    1,
)

MUTATIONS = [
    ("就绪后仍复述过期原因", BROKEN_IGNORES_CURRENT_STATE),
    ("没配好也说已经配好", BROKEN_ALWAYS_READY),
    ("能不能用不再跟着状态走", BROKEN_READY_IS_STATIC),
    ("按钮和提示各写一份判定", BROKEN_JOBS_SPLIT_JUDGEMENT),
]


def run_test() -> tuple[int, str]:
    finished = subprocess.run(
        ["node", "--test", str(TEST)], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return finished.returncode, (finished.stdout or "") + (finished.stderr or "")


def main() -> int:
    failures = []
    try:
        # 对照组：什么都不改，测试必须是绿的。
        code, output = run_test()
        if code != 0:
            print("对照失败：未改动实现时测试本应通过")
            print(output[-1500:])
            return 1
        print("[对照组] 未改动实现 → 测试通过 ✓")

        for label, mutated in MUTATIONS:
            if mutated == ORIGINAL:
                print(f"[{label}] 变异没有真正改到代码，跳过（检查替换点）")
                failures.append(label)
                continue
            LIBRARY.write_text(mutated, encoding="utf-8")
            code, output = run_test()
            if code == 0:
                failures.append(label)
                print(f"[{label}] 坏实现竟然通过了测试 —— 这个测试没守住")
            else:
                first = next(
                    (line.strip() for line in output.splitlines() if line.strip().startswith("AssertionError")), ""
                )
                print(f"[{label}] 测试如期失败 ✓ {first[:90]}")
    finally:
        LIBRARY.write_text(ORIGINAL, encoding="utf-8")

    restored = LIBRARY.read_text(encoding="utf-8")
    if restored != ORIGINAL:
        print("严重：实现文件没能还原成原样")
        return 1
    print("实现已还原")

    code, _ = run_test()
    if code != 0:
        print("还原后测试没有恢复通过")
        return 1
    print("还原后测试仍通过 ✓")

    if failures:
        print(f"\n{len(failures)} 个变异没被测试挡住：{failures}")
        return 1
    print("\n所有变异都如期被测试挡住 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

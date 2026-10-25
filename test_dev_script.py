"""dev.ps1 的「check」必须把子任务的失败传出去。

1.26.30 实测发现的漏洞：「check」把 lint / type / test 三个子任务当独立进程跑，
却从来不看它们的退出码 —— 有一版前端测试红了 3 条，脚本照样打印「全过了」。
老师跑一遍「全过了」，心里就踏实了 —— 结果是假的。这跟「导出的东西必须和
屏幕上核对过的完全一样」是同一类毛病：说成功就得真的成功。

这一版：
  1. check 每跑完一个子任务就查退出码，不通过立刻停手；
  2. 加一条真跑的行为测试：拿一份临时副本，把第一个子任务换成一条必定失败的
     命令，跑一遍 check，看它到底会不会失败、会不会还说「全过了」。
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent
SCRIPT = REPO / "dev.ps1"
POWERSHELL = "powershell"


class DevScriptCheckTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "dev.ps1 得在仓库根目录")
        self.text = SCRIPT.read_text(encoding="utf-8-sig")

    def test_check_looks_at_each_sub_task_exit_code(self):
        """check 调完每个子任务都要看退出码 —— 不看就会把失败说成「全过了」。"""
        block = self.text.split('"check" {', 1)[-1].split("\n  }", 1)[0]
        self.assertIn("-Task lint", block)
        self.assertIn("-Task type", block)
        self.assertIn("-Task test", block)
        for task in ("lint", "type", "test"):
            self.assertRegex(
                block, r"-Task " + task + r"\s*\r?\n\s*if \(\$LASTEXITCODE",
                f"「{task}」跑完必须查退出码",
            )

    def test_a_failing_sub_task_stops_check_and_never_says_all_good(self):
        """真跑一遍：把第一个子任务换成必定失败的命令，check 必须失败。"""
        if not shutil_which(POWERSHELL):
            self.skipTest("这台机器上没有 powershell")
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / "dev.ps1"
            copy.write_text(
                self.text.replace(
                    '& $MyInvocation.MyCommand.Path -Task lint',
                    "& cmd /c exit 3",
                    1,
                ),
                encoding="utf-8-sig",
            )
            result = subprocess.run(
                [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(copy), "-Task", "check"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(REPO),
                timeout=180,
            )
        self.assertNotEqual(result.returncode, 0, "子任务失败了，check 必须失败")
        self.assertNotIn("全过了", (result.stdout or "") + (result.stderr or ""),
                         "失败了还打印「全过了」，等于骗人")


def shutil_which(name):
    from shutil import which

    return which(name)


if __name__ == "__main__":
    unittest.main()

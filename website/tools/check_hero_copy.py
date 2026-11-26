"""首屏改版后的文案核对：旧版文案必须全部保留在新版里。

对比基线 = git HEAD 的 website/public/index.html，当前版 = 工作区文件。

做法：
1. 去掉标签、压掉空白，取两版的纯文本
2. 把文本按中文标点 / 常见分隔符切成语义短语
3. 逐条检查旧版短语是否仍出现在新版文本中
4. 打印新增短语（应该只有首屏刻意加的那几条）

在仓库根目录执行：
    python website/tools/check_hero_copy.py
"""

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX = REPO_ROOT / "website" / "public" / "index.html"

# 切分语义短语的符号：中文标点 + 英文标点 + 中点 + 换行。
# 注意不要切 "." 和 "/"：「tiyouju.com」「Windows 10 / 11」这类必须保持完整。
SPLIT_RE = re.compile(r"[，。、；：！？,;:!?（）()【】\[\]|·]+|\n+")

failures: list[str] = []


def plain_text(html: str) -> str:
    """去掉标签与 script/style，取可见文本并压掉多余空白。"""
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<[^>]+>", "\n", html)
    # 只压掉行内空白，保留换行 —— 换行正是「块与块之间」的边界
    return re.sub(r"[ \t\r]+", " ", html).strip()


def phrases(text: str) -> list[str]:
    """切成语义短语并去重（保留出现顺序）。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in SPLIT_RE.split(text):
        p = raw.strip()
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def main() -> int:
    old_html = subprocess.run(
        ["git", "show", "HEAD:website/public/index.html"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    new_html = INDEX.read_text(encoding="utf-8")

    old_text = plain_text(old_html)
    new_text = plain_text(new_html)

    old_phrases = phrases(old_text)
    new_phrases = phrases(new_text)

    missing = [p for p in old_phrases if p not in new_text]
    added = [p for p in new_phrases if p not in old_text]

    print(f"旧版短语 {len(old_phrases)} 条 / 新版短语 {len(new_phrases)} 条")
    print(f"旧版可见字符 {len(old_text)} / 新版可见字符 {len(new_text)}")

    if missing:
        print("\n丢失的旧文案：")
        for p in missing:
            print(f"  MISSING  {p}")
        failures.append(f"丢失 {len(missing)} 条旧文案")
    else:
        print("\n  OK   旧版文案 100% 保留，无丢失")

    if added:
        print("\n新版新增文案（首屏设计元素）：")
        for p in added:
            print(f"  ADDED   {p}")

    if failures:
        print("\nFAILED: " + "; ".join(failures))
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

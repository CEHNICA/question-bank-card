# -*- coding: utf-8 -*-
"""现有识读期守卫在真实题库上的覆盖率自查。

为什么要有这个脚本：判断「还缺哪条判据」需要一个基准。以前每次改判据只能靠
印象，没有一份数字能回答「这条守卫在真实数据上到底咬到多少」。改完判据之后
跑一遍，数字不动就说明接线没走到真实路径上（单元测试全绿但接线点错的情形）。

它读真实数据库，不写任何东西。只在本地开发时手工运行，不参与单元测试。

    .\\backend\\.venv\\Scripts\\python.exe backend\\core\\audit_flag_coverage.py

2026-10-11 首次量的结果（14 份 ready 资料、959 张卡）：
九条守卫共咬到 57 张，其中 37 张是绿卡零旗标（= 用户完全看不到的真实缺陷），
占全库 720 张绿卡零旗标的 5.1%。新增的三条贡献了其中大部分：填空题无可填
空格 30 张、印刷解答混入题干 6 张。其余六条在这份题库上是 0–3 张，属于
「有守卫、无现实缺陷」的状态，不是死代码（逐条把该抓的形状喂进去都能咬到）。
"""
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")

import django  # noqa: E402

django.setup()

from core import pipeline, qtypes, textnorm  # noqa: E402
from core.models import Paper, Question  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# 每条判据只做一件事：给它一张卡，返回是否命中。
# 必须直接复用实现，不要在这里复刻一份正则 —— 复刻的那份在判据改动后会安静地
# 报旧数字，不报错也不失败，比测试红更难发现。
GUARDS = {
    "题型标成填空题却无可填空格":
        lambda c: qtypes.fill_blank_without_slot(c.question_type, c.stem),
    "题干里混进了印刷解答":
        lambda c: bool(textnorm.printed_solution_in_stem("", c.stem)),
    "题干里混进了下一节的题号":
        lambda c: bool(textnorm.merged_question_number(c.stem, c.number)),
    "大题标题与题型冲突":
        lambda c: bool(pipeline._section_kind_conflict(c.section, c.question_type)),
    "选项字母断档":
        lambda c: bool(pipeline._option_gaps(c.options or {}, c.figures or [])),
    "读出了第 5 个及以后的选项":
        lambda c: bool(pipeline._surplus_options(c.options or {}, c.figures or [])),
    "两个选项读成一样":
        lambda c: bool(pipeline._identical_options(c.options or {})),
    "标题说选择题但一个选项都没有":
        lambda c: pipeline._section_choice_without_options(
            str(c.section or ""), c.options or {}, c.figures or []),
    "坐标只读到一半":
        lambda c: bool(pipeline._broken_coordinate(str(c.stem or ""))),
}


def main() -> int:
    papers = list(Paper.objects.filter(status="ready"))
    cards = [c for p in papers for c in Question.objects.filter(paper=p)]
    print(f"ready 资料 {len(papers)} 份，卡 {len(cards)} 张\n")
    print(f"{'判据':<32}{'咬到':>6}{'其中已黄':>10}{'绿卡零旗标':>12}")
    print("-" * 60)
    rows = []
    for name, hit_test in GUARDS.items():
        hits = [c for c in cards if hit_test(c)]
        yellow = sum(1 for c in hits if c.state == "yellow")
        silent = sum(1 for c in hits if c.state == "green" and not c.flags)
        rows.append((name, len(hits), yellow, silent))
    for name, total, yellow, silent in sorted(rows, key=lambda r: -r[3]):
        print(f"{name:<32}{total:>6}{yellow:>10}{silent:>12}")

    total_hits = sum(r[1] for r in rows)
    total_silent = sum(r[3] for r in rows)
    green = [c for c in cards if c.state == "green" and not c.flags]
    print(f"\n合计命中 {total_hits} 张，其中 {total_silent} 张是绿卡零旗标。")
    print(f"全库绿卡零旗标 {len(green)} 张，守卫覆盖 {total_silent} 张 "
          f"（{total_silent / max(len(green), 1):.1%}）。")
    print("\n「绿卡零旗标」那一栏才是用户完全看不到的缺陷；已黄的那部分是重复提示。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

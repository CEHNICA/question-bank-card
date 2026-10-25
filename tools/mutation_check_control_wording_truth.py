"""Mutation check for controls that must explain themselves.

Two screens were walking-through findings: the settings page never said what
this computer can do right now, and the details-panel toggle was a switch
whose name never changed with its state. Both are copy plus wiring, so the
mutations below aim at what the reader actually gets - the sentence, the
hover line, the clickable height - not at whether a string exists in the file.

Same rules as the other mutation checks: revert before the next mutation, and
a missing anchor stops the run instead of reporting a clean sweep.

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\mutation_check_control_wording_truth.py
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
NODE = "node"
SETTINGS = ["node", "test_settings_ui.js"]
SIDEBAR = ["node", "test_library_context_sidebar.js"]

READINESS_LINE = '<p id="settingsServiceReadiness" class="settings-readiness" role="status">正在查看这台电脑的配置…</p>'
MINERU_LINE = ('sentences.push(status.mineru ? "自动切题用 MinerU，已经配好。" '
               ': "自动切题还没配 MinerU，上传后需要手工切题。");')
TONE_LINE = 'return { tone: status.mineru && reading && reading.ready ? "ready" : "missing", text: sentences.join("") };'
READER_BRANCH = ('`识读和生成答案还没配服务${reading && reading.label ? `（现在选的是「${reading.label}」但没有密钥）` : ""}，不影响上传和手工切题。`')
TOGGLE_TITLE = ('toggle.title = disabled ? "先在左边点一道题，这里才有它的来源、标签和待核对建议"\n'
                '      : `${label}：当前题目的来源、标签和待核对建议都写在这一栏`;')
TOGGLE_STATE = "const on = Boolean(open) && !disabled;"
TOGGLE_LABEL = 'const label = disabled ? "先选中一道题" : (on ? "收起右侧详情" : "展开右侧详情");'
SUMMARY_RULE = ('.library-page .question-inspector-preview > summary,\n'
                '  .library-page .question-inspector-info > summary { display: flex; align-items: center; min-height: 30px; }')

# (file, description, original, mutation, command)
MUTATIONS = [
    ("frontend/index.html",
     "the API tab goes back to answering nothing and only offering a button",
     READINESS_LINE,
     "",
     SETTINGS),

    ("frontend/app.js",
     "a missing MinerU key stops being named, the page claims it is fine",
     MINERU_LINE,
     'sentences.push("自动切题用 MinerU，已经配好。");',
     SETTINGS),

    ("frontend/app.js",
     "the page always reports everything as ready",
     TONE_LINE,
     'return { tone: "ready", text: sentences.join("") };',
     SETTINGS),

    ("frontend/app.js",
     "a chosen-but-keyless service is no longer named",
     READER_BRANCH,
     '`识读和生成答案还没配服务，不影响上传和手工切题。`',
     SETTINGS),

    ("frontend/app.js",
     "refreshing the status leaves that line on whatever it first said",
     "    renderSettingsModels();\n    renderServiceReadiness();",
     "    renderSettingsModels();",
     SETTINGS),

    ("frontend/library.js",
     "the details toggle stops saying what lives in that panel",
     TOGGLE_TITLE,
     'toggle.title = disabled ? "先在左边点一道题" : label;',
     SIDEBAR),

    ("frontend/library.js",
     "a greyed-out details toggle reports itself as open",
     TOGGLE_STATE,
     "const on = Boolean(open);",
     SIDEBAR),

    ("frontend/library.js",
     "the toggle goes back to a fixed noun that never changes with its state",
     TOGGLE_LABEL,
     'const label = "题目详情";',
     SIDEBAR),

    ("frontend/library-reading.css",
     "the second disclosure row goes back to being half the height of the first",
     SUMMARY_RULE,
     '.library-page .question-inspector-preview > summary { display: flex; align-items: center; min-height: 30px; }',
     SIDEBAR),
]


def run(command):
    return subprocess.run(command, cwd=str(FRONTEND), capture_output=True, text=True, encoding="utf-8", errors="replace")


def apply(source: str, original: str, mutation: str) -> str:
    """Swap one anchor, matching whether this file uses \\n or \\r\\n.

    Anchors are written with \\n so they stay readable in source. A file saved
    with CRLF therefore has to be matched in its CRLF form too.
    """
    if original in source:
        return source.replace(original, mutation, 1)
    crlf_anchor = original.replace("\n", "\r\n")
    if crlf_anchor in source:
        return source.replace(crlf_anchor, mutation.replace("\n", "\r\n"), 1)
    raise SystemExit(f"anchor not found, the check has gone stale:\n{original}")


def main():
    results = []
    for relative, description, original, mutation, command in MUTATIONS:
        target = ROOT / relative
        # 1.25.11：这一轮的变异跨了两种行尾 —— frontend/app.js 是 LF，index.html /
        # library.js / library-reading.css 都是 CRLF。read_text() 把两边都归一成 \n
        # 所以锚点照常匹配得上，可 write_text() 在 Windows 上写回时会一律改成 CRLF，
        # app.js 一次跑完就从 9500 行 LF 变成 9500 行 CRLF：diff 全红，还顺手把靠源码
        # 切片当门禁的测试重新废掉一次（标记写死 \n 时 indexOf 返回 -1，slice 静默切出
        # 空串）。所以这里全程走字节，还原时写回原始内容，一个 bit 都不动。
        raw = target.read_bytes()
        source = raw.decode("utf-8")
        mutated = apply(source, original, mutation)
        if mutated == source:
            raise SystemExit(f"{relative}: anchor not found, the check has gone stale:\n{original}")
        baseline = run(command)
        if baseline.returncode != 0:
            print(baseline.stdout[-3000:], baseline.stderr[-3000:])
            raise SystemExit(f"{relative}: the unmutated suite already fails, refusing to trust this run")
        target.write_bytes(mutated.encode("utf-8"))
        try:
            broken = run(command)
        finally:
            target.write_bytes(raw)
        caught = broken.returncode != 0
        results.append((description, caught))
        print(("挡住 ✓  " if caught else "漏过 ✗  ") + description)
        if not caught:
            print((broken.stdout or "")[-2000:] + (broken.stderr or "")[-2000:])
    survived = [item for item in results if not item[1]]
    print(f"\n{len(results) - len(survived)}/{len(results)} 条变异均被挡住" if not survived
          else f"\n{len(survived)} 条变异漏过：{survived}")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())

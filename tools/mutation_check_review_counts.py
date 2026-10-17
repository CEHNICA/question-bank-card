"""Mutation check for the review-count / re-read wording changes.

Each case reverts one change in place, runs the new test, and expects it to
fail. Files are snapshotted first and restored byte-exact afterwards.

Everything goes through bytes on purpose: read_text()/write_text() would
translate CRLF to LF and leave the working copy dirty after a restore.
"""
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

root = pathlib.Path(__file__).resolve().parents[1]
files = ["frontend/app.js", "frontend/index.html", "frontend/styles.css"]
backup = {f: (root / f).read_bytes() for f in files}


def restore():
    for name, data in backup.items():
        (root / name).write_bytes(data)


def run():
    proc = subprocess.run(
        ["node", "frontend/test_review_counts.js"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root,
    )
    return proc.returncode, (proc.stderr or proc.stdout).strip().splitlines()


def mutate(name, old, new):
    path = root / name
    text = path.read_bytes().decode("utf-8")
    # 1.25.11：这三个目标文件行尾不统一 —— app.js 和 styles.css 是 LF，index.html 是
    # CRLF。锚点为了好读都写成 \n，于是在 index.html 上永远匹配不上，脚本会一路崩在
    # 第三条上，看起来像"这个检查没跑"，其实早就跑不了了。所以 \r\n 也试一次。
    if old in text:
        path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        return
    crlf_old, crlf_new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    assert crlf_old in text, f"anchor missing in {name}: {old[:40]!r}"
    path.write_bytes(text.replace(crlf_old, crlf_new, 1).encode("utf-8"))


CASES = [
    (
        "把重复的那句话放回去",
        "frontend/app.js",
        "    } else if (c.todo || c.green) {",
        "    } else if (c.todo) {\n"
        "      statusText.textContent = `${c.all} 道题，${c.todo} 题需要核查。`;\n"
        "    } else if (c.green) {",
    ),
    (
        "把永远不显示的图例节点加回来",
        "frontend/index.html",
        "          </details>\n        </div>",
        '          </details>\n          <div class="meter-legend" id="meterLegend" hidden></div>\n        </div>',
    ),
    (
        "去掉「让 AI 重读这题」的悬停说明",
        "frontend/app.js",
        ', "重读这道题的全部片段并替换现有文字（改过的地方会先问你）；只纠一小块用「框选识读（纠错）」"));',
        "));",
    ),
]

all_failed = True
try:
    for label, name, old, new in CASES:
        restore()
        mutate(name, old, new)
        code, out = run()
        reason = next((ln for ln in out if "AssertionError" in ln), out[0] if out else "")
        print(f"[{'PASS' if code else 'FAIL'}] {label} -> exit {code}")
        print(f"       {reason.strip()[:160]}")
        all_failed = all_failed and code != 0
finally:
    restore()

intact = all((root / f).read_bytes() == b for f, b in backup.items())
print(f"\nrestore byte-exact: {intact}")
final, _ = run()
print(f"after restore exit: {final}")
sys.exit(0 if all_failed and intact and final == 0 else 1)

"""List every mutation script, the files it rewrites, and how those files end.

Several checks rewrite source files in place. A plain write_text() on Windows
turns an LF file into CRLF, which shows up as a whole-file diff and quietly
re-arms the source-slicing tests. Three checks were fixed for this one at a
time (control_wording, review_counts, and this round's progress_wording); this
lists what is left instead of waiting for the next one to trip.

    .\\packaging\\.build\\venv\\Scripts\\python.exe tools\\audit_mutation_line_endings.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

PATH_RE = re.compile(r'"((?:frontend|backend|packaging|docs|tools)/[A-Za-z0-9_./-]+|[A-Za-z0-9_./-]+\.(?:py|js|css|html|md))"')


def eol(path: Path) -> str:
    data = path.read_bytes()
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if not lf and not crlf:
        return "无换行"
    if not crlf:
        return "LF"
    if not lf:
        return "CRLF"
    return f"混排(CRLF {crlf} / LF {lf})"


def main() -> int:
    risky = []
    for script in sorted(TOOLS.glob("mutation_check_*.py")):
        text = script.read_text(encoding="utf-8", errors="replace")
        safe_write = "newline=" in text or "write_bytes" in text
        targets = sorted({m for m in PATH_RE.findall(text) if (ROOT / m).is_file()})
        lf_targets = [t for t in targets if eol(ROOT / t) == "LF"]
        state = "安全（按字节写或显式 newline=）" if safe_write else "用 write_text，未指定 newline"
        print(f"\n{script.name}\n  写法：{state}")
        for target in targets:
            kind = eol(ROOT / target)
            flag = " ← 会被改成 CRLF" if (not safe_write and kind == "LF") else ""
            print(f"    {kind:6} {target}{flag}")
        if not safe_write and lf_targets:
            risky.append((script.name, lf_targets))

    print("\n" + "=" * 60)
    if risky:
        print("这些脚本会用 write_text 把 LF 文件写成 CRLF：")
        for name, targets in risky:
            print(f"  {name} → {', '.join(targets)}")
        return 1
    print("没有脚本会再用 write_text 改写 LF 文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

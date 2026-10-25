"""Normalise working-copy line endings to what .gitattributes asks for.

`*.js` / `*.py` / `*.css` / `*.html` are all `eol=lf` in .gitattributes. A tool
that rewrites a file in text mode on Windows can hand back a CRLF copy, which
then shows up as a whole-file diff. Git would normalise it on commit, but the
working tree should still match the convention.

Usage: python tools/normalize_eol.py [--check]
"""

import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

root = pathlib.Path(__file__).resolve().parents[1]
CRLF = b"\r\n"

names = subprocess.run(
    ["git", "ls-files", "*.js", "*.py", "*.css", "*.html"], capture_output=True, text=True, cwd=root, check=True
).stdout.split()

check_only = "--check" in sys.argv
offenders = []
for name in names:
    path = root / name
    if not path.is_file():
        continue
    data = path.read_bytes()
    if CRLF not in data:
        continue
    fixed = data.replace(CRLF, b"\n")
    offenders.append((name, data.count(CRLF)))
    if not check_only:
        path.write_bytes(fixed)

if offenders:
    print(f"{'would fix' if check_only else 'fixed'}: {len(offenders)} file(s)")
    for name, count in offenders[:20]:
        print(f"  {name}: {count} CRLF")
else:
    print("all tracked js/py/css/html are LF")
sys.exit(1 if check_only and offenders else 0)

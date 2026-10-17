"""Mutation check: the window-button fix must fail when it is undone.

The reported symptom was an open window whose own minimise and close buttons did
nothing. The cause was this launcher opening the window onto an instance it did
not own and then returning at once, which ended the process and with it the
thread that carries those buttons out. Each mutation below puts that back and
requires the tests to go red.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"C:\Users\17321\.minimax\sessions\mvs_fc946281665f4565a3d0ce7af01bd838\workspace\qbj-src")
TARGET = ROOT / "app_window.py"
TESTS = "test_app_window"

MUTATIONS = [
    (
        "the launcher returns at once and the watcher dies with it",
        "        wait_for_reattached_window(window)\n        return 0",
        "        return 0",
    ),
    (
        "the helper gives up immediately instead of watching the window",
        "    profile = window_profile()\n"
        "    started = time.monotonic()\n"
        "    while window.poll() is None:\n"
        "        time.sleep(0.5)",
        "    profile = window_profile()\n"
        "    started = time.monotonic()\n"
        "    while False:\n"
        "        time.sleep(0.5)",
    ),
]

# 1.25.11：这一版全程走字节。原来读一次 read_text、还原一次 write_text，在 Windows 上
# 会把 app_window.py 从 LF 重写成 CRLF；而收尾那句
#     if TARGET.read_text(...) != original
# 恰恰抓不到 —— read_text 会把 CRLF 归一回 \n，两边照样相等，于是它一边把行尾改了一边
# 报告「还原成功」。既要是真的逐字节还原，就不能拿会归一的读法去比。
raw = TARGET.read_bytes()
original = raw.decode("utf-8")
backup = Path(tempfile.mkdtemp()) / "app_window.py"
shutil.copy2(TARGET, backup)

failed = 0
try:
    for label, old, new in MUTATIONS:
        if original.count(old) != 1:
            print(f"[BAD ] {label}: pattern found {original.count(old)} times, need exactly 1")
            failed += 1
            continue
        TARGET.write_bytes(raw.replace(old.encode("utf-8"), new.encode("utf-8"), 1))
        result = subprocess.run(
            [str(ROOT / "packaging" / ".build" / "venv" / "Scripts" / "python.exe"),
             "-m", "unittest", TESTS],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        out = result.stdout + result.stderr
        names = sorted({line.split("]")[-1].strip()
                        for line in out.splitlines() if line.startswith("FAIL:")})
        if result.returncode != 0:
            print(f"[PASS] {label}")
            for name in names:
                print(f"         caught by: {name}")
        else:
            print(f"[FAIL] {label}: tests still passed, so nothing checks this")
            failed += 1
finally:
    TARGET.write_bytes(raw)
    if TARGET.read_bytes() != raw:
        print("[BAD ] source was not restored byte for byte")
        failed += 1

print()
print("all mutations caught" if failed == 0 else f"{failed} problem(s)")
sys.exit(1 if failed else 0)

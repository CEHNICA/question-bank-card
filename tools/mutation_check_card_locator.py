"""Mutation check for the card-locator placement fix.

The locator used to be appended to the card header with no class, so the three
column grid auto-placed it into the 18px checkbox column and stacked every
character vertically. Two mutations, each reintroducing that shape; the test
must go red both times.
"""
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

root = pathlib.Path(__file__).resolve().parents[1]
target = root / "frontend/library.js"
original = target.read_bytes()

FIXED_APPEND = "    meta.append(select, heading, sourceName, provenance);"
FIXED_INTO_PROVENANCE = (
    '    if (shortSection(item)) provenance.append(node("span", "", cardLocator(item)));'
)

REGRESSED_APPEND = (
    "    meta.append(select, heading, sourceName, node(\"span\", \"\", cardLocator(item)), provenance);"
)


def run():
    proc = subprocess.run(
        ["node", "--test", "frontend/test_library_card_locator.js"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root,
    )
    out = proc.stdout + proc.stderr
    reason = next((ln.strip() for ln in out.splitlines()
                   if "AssertionError" in ln or "不能作为没有类名" in ln), "")
    return proc.returncode, reason[:150]


try:
    text = original.decode("utf-8")
    assert FIXED_APPEND in text, "fixed meta.append not found"
    assert FIXED_INTO_PROVENANCE in text, "fixed provenance.append not found"

    cases = [
        ("把定位句又挂回卡头（竖排回归）",
         text.replace(FIXED_APPEND, REGRESSED_APPEND).replace(FIXED_INTO_PROVENANCE, "")),
        ("无条件显示定位句（试卷卡开始重复念题号）",
         text.replace(FIXED_INTO_PROVENANCE,
                      '    provenance.append(node("span", "", cardLocator(item)));')),
    ]
    all_red = True
    for label, mutated in cases:
        assert mutated != text, f"mutation had no effect: {label}"
        target.write_bytes(mutated.encode("utf-8"))
        code, reason = run()
        print(f"[{'PASS' if code else 'FAIL'}] {label} -> exit {code}")
        if reason:
            print(f"       {reason}")
        all_red = all_red and code != 0
finally:
    target.write_bytes(original)

print(f"\nrestored byte-exact: {target.read_bytes() == original}")
final, _ = run()
print(f"after restore exit: {final}")
sys.exit(0 if all_red and final == 0 else 1)

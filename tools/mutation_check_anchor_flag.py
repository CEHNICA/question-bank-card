"""Mutation check for the anchor-flag test that used to pass vacuously.

Two mutations, each neutering one of the two code paths the test claims to
tie together. The test must go red both times; if it stays green it is not
actually holding the invariant down.
"""
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

root = pathlib.Path(__file__).resolve().parents[1]
pipeline = root / "backend/core/pipeline.py"
venv = root / "packaging/.build/venv/Scripts/python.exe"
target = "core.test_anchor_flag_reconcile.AnchorReadingFlagReconcileTests" \
         ".test_read_card_and_this_pass_say_the_same_thing"

original = pipeline.read_bytes()

READ_CARD_ANCHOR = (
    '        flags.append(\n'
    '            f"这张卡题号那一行印着「{probe}…」，但识读出来的内容里认不出这句话，"\n'
    '            f"卡上可能是另一道题；请对照原书确认这张卡的范围和内容")'
)
RECONCILE_ANCHOR = (
    '            f"这张卡题号那一行印着「{probe}…」，但识读出来的内容里认不出这句话，"'
)


def run():
    proc = subprocess.run(
        [str(venv), "manage.py", "test", target],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=root / "backend",
    )
    out = proc.stdout + proc.stderr
    reason = next((ln for ln in out.splitlines() if "AssertionError" in ln), "")
    return proc.returncode, reason.strip()[:150]


def write(text):
    pipeline.write_bytes(text.encode("utf-8"))


try:
    text = original.decode("utf-8")
    assert READ_CARD_ANCHOR in text, "read_card anchor flag block not found"
    assert RECONCILE_ANCHOR in text, "reconcile anchor flag text not found"

    cases = [
        ("识读那一刻不再挂这条提示", text.replace(READ_CARD_ANCHOR, "        pass")),
        ("事后补的那一遍改了措辞，两处对不上",
         text.replace(RECONCILE_ANCHOR, '            f"事后补：印着「{probe}…」，认不出来，"')),
    ]
    all_red = True
    for label, mutated in cases:
        assert mutated != text, f"mutation had no effect: {label}"
        write(mutated)
        code, reason = run()
        print(f"[{'PASS' if code else 'FAIL'}] {label} -> exit {code}")
        if reason:
            print(f"       {reason}")
        all_red = all_red and code != 0
finally:
    pipeline.write_bytes(original)

print(f"\nrestored byte-exact: {pipeline.read_bytes() == original}")
final, _ = run()
print(f"after restore exit: {final}")
sys.exit(0 if all_red and final == 0 else 1)

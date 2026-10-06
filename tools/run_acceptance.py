"""Run every acceptance script with the environment it actually needs.

Why this exists: the checks were run by hand with an ad-hoc loop, and that loop
quietly gave several of them the wrong arguments. Three scripts were pointed at
a server that was never seeded for them, and four were missing their required
--paper, so they died on argparse before testing anything while the sweep still
read as "green". A sweep that cannot tell "passed" from "never ran" is worse than
no sweep.

This runner is the single entry point:

    .\\backend\\.venv\\Scripts\\python.exe tools\\run_acceptance.py

It discovers tools/check_*.py, gives each one its own arguments and server,
prints one line per script, and exits non-zero if anything failed. Scripts it
cannot honestly run are printed as SKIP with the reason -- never as ok.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
PYTHON = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
LOGS = ROOT / "tmp" / "sweep"
# 脚本自己说「测不了」的退出码。别用 2：argparse 参数错误也是 2。
SKIP_EXIT = 3

# 需要打包产物才能查的（题有据.exe + tiyouju.exe），日常跑不了。
BUNDLE_ONLY = {"check_assistant_setup"}
# 查的是已安装到桌面的那份（8768），不是开发服务。
INSTALLED_ONLY = {"check_installed_tick"}
# 写草稿，需要先 --seed 再起一份只属于它自己的服务。
FIXTURE_SCRIPTS = ("check_library_ux_browser", "check_library_workspace_browser")
# 这两个把 BASE 写死在 8804，要指向 tmp/accept-review。
REVIEW_PORT = 8804
REVIEW_ROOT = ROOT / "tmp" / "accept-review"
FIXTURE_ROOT = ROOT / "tmp" / "sweep-ux-fixture"

# 默认只跑没有必填参数的；有 --paper 的按需补给。
NEEDS_PAPER = {"check_fullscreen_editors", "check_fullscreen_nav", "check_review_restore", "check_tick_roundtrip"}
RUN_FLAG = {"check_edit_assistance_browser", "check_library_counts", "check_library_recovery_browser",
            "check_model_settings_browser",
            "check_page_canvas_browser", "check_print_scroll_browser", "check_practice_browser",
            "check_source_pan_browser", "check_task_delete_after_withdraw", "check_unsaved_browser"}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def reachable(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def start_server(port: int, root: Path, database: Path, log_name: str):
    """Start a Django dev server on its own data root; return the process."""
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               QB_DATA_ROOT=str(root), QB_DATABASE=str(database))
    LOGS.mkdir(parents=True, exist_ok=True)
    handle = (LOGS / f"{log_name}.out").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(PYTHON), "manage.py", "runserver", str(port), "--noreload"],
        cwd=str(ROOT / "backend"), env=env, stdout=handle, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        if reachable(base + "/", timeout=2):
            return process
        if process.poll() is not None:
            raise SystemExit(f"{log_name} 服务起不来，见 tmp/sweep/{log_name}.out")
        time.sleep(0.5)
    process.terminate()
    raise SystemExit(f"{log_name} 服务 40 秒没起来")


def first_paper(base: str) -> str | None:
    try:
        with urllib.request.urlopen(f"{base}/api/papers", timeout=15) as response:
            papers = json.load(response).get("papers", [])
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    ready = [row for row in papers if row.get("status") == "ready"]
    return (ready or papers or [None])[0] and ((ready or papers)[0]["id"])


def run_script(name: str, args: list[str], env: dict | None = None):
    LOGS.mkdir(parents=True, exist_ok=True)
    started = time.time()
    finished = subprocess.run([str(PYTHON), str(TOOLS / f"{name}.py"), *args],
                              cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              env=dict(os.environ, PYTHONIOENCODING="utf-8", **(env or {})), timeout=1800)
    output = (finished.stdout or "") + (finished.stderr or "")
    if finished.returncode != 0:
        (LOGS / f"{name}.log").write_text(output, encoding="utf-8")
    return finished.returncode, output, time.time() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8803")
    parser.add_argument("--only", nargs="*", help="run just these check names")
    parser.add_argument("--bundle", type=Path, help="audited bundle for check_assistant_setup")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    names = sorted(path.stem for path in TOOLS.glob("check_*.py"))
    if args.only:
        names = [name for name in names if name in args.only]

    results: list[tuple[str, str, str]] = []
    servers: list[tuple[str, subprocess.Popen]] = []
    try:
        if not reachable(base + "/", timeout=5):
            print(f"开发服务 {base} 没在跑；先把它起起来（或用 --base 指到别的端口）。")
            return 2
        paper = first_paper(base)

        # 自己那两份 fixture 服务：每次重建，免得上一次留下的行混进来。
        fixture_url = None
        if any(name in FIXTURE_SCRIPTS for name in names):
            if FIXTURE_ROOT.exists():
                import shutil
                shutil.rmtree(FIXTURE_ROOT, ignore_errors=True)
            port = free_port()
            process = start_server(port, FIXTURE_ROOT, FIXTURE_ROOT / "db.sqlite3", "ux-fixture")
            servers.append(("ux-fixture", process))
            fixture_url = f"http://127.0.0.1:{port}"
            code, output, _ = run_script("check_library_ux_browser", ["--seed"],
                                         env={"QB_DATA_ROOT": str(FIXTURE_ROOT), "QB_DATABASE": str(FIXTURE_ROOT / "db.sqlite3")})
            if code != 0:
                print("fixture seed 失败：\n" + output)
                return 2

        review_url = None
        if "check_paper_stale_question" in names or "check_stale_solution_restore" in names:
            if reachable(f"http://127.0.0.1:{REVIEW_PORT}/", timeout=2):
                review_url = f"http://127.0.0.1:{REVIEW_PORT}"
            else:
                process = start_server(REVIEW_PORT, REVIEW_ROOT, REVIEW_ROOT / "db.sqlite3", "review")
                servers.append(("review", process))
                review_url = f"http://127.0.0.1:{REVIEW_PORT}"

        for name in names:
            if name in BUNDLE_ONLY and not args.bundle:
                results.append((name, "SKIP", "要打包产物才跑得了（给 --bundle）"))
                continue
            if name in INSTALLED_ONLY:
                results.append((name, "SKIP", "查的是已安装到桌面的那份，不是开发服务"))
                continue
            extra: list[str] = []
            env: dict[str, str] = {}
            if name in FIXTURE_SCRIPTS:
                extra = ["--url", fixture_url]
            elif name in ("check_paper_stale_question", "check_stale_solution_restore"):
                if review_url != f"http://127.0.0.1:{REVIEW_PORT}":
                    results.append((name, "SKIP", f"这两个脚本把端口写死在 {REVIEW_PORT}，而那个端口上不是 accept-review 的服务"))
                    continue
            elif name == "check_archived_reimport":
                env = {"QB_DATABASE": str(ROOT / "tmp" / "accept-1133" / "db.sqlite3"),
                       "QB_DATA_ROOT": str(ROOT / "tmp" / "accept-1133")}
            elif name == "check_assistant_setup":
                extra = ["--bundle", str(args.bundle)]
            elif name in NEEDS_PAPER:
                if not paper:
                    results.append((name, "SKIP", "拿不到试卷 id"))
                    continue
                extra = ["--paper", paper]
            if name in RUN_FLAG:
                extra.append("--run")
            if not any(flag.startswith("--url") or flag.startswith("--base") for flag in extra):
                extra = ["--url", base, *extra] if "--url" in _declared(name) else extra

            code, output, seconds = run_script(name, extra, env=env)
            # 退出码 3 = 脚本自己说「这份数据/这版界面测不了」。它跟跑挂了不是一回事，
            # 混在 FAIL 里会让整张表看不出哪些是真问题。注意别用 2：argparse 的参数
            # 错误也是 2，那是真的没跑起来。
            if code == 0:
                status = "ok"
            elif code == SKIP_EXIT:
                status = "SKIP"
            else:
                status = "FAIL"
            note = "" if code == 0 else (output.strip().splitlines() or [""])[-1][:70]
            results.append((name, status, f"{seconds:.0f}s {note}".strip()))

        width = max(len(name) for name, _, _ in results)
        for name, status, note in results:
            print(f"{name.ljust(width)}  {status:5} {note}".rstrip())
        failed = [name for name, status, _ in results if status == "FAIL"]
        skipped = [name for name, status, _ in results if status == "SKIP"]
        print(f"\n{len(results) - len(failed) - len(skipped)} ok / {len(failed)} 失败 / {len(skipped)} 跳过"
              + (f"（跳过：{', '.join(skipped)}）" if skipped else ""))
        return 1 if failed else 0
    finally:
        for _, process in servers:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def _declared(name: str) -> set[str]:
    text = (TOOLS / f"{name}.py").read_text(encoding="utf-8")
    return {"--url"} if '"--url"' in text else set()


if __name__ == "__main__":
    sys.exit(main())
"""Internal entry points used by the self-contained Windows executable.

The public desktop process is built as a windowed executable.  Windowed Python
processes can have ``sys.stdout`` set to ``None``, so every internal role opens
its own UTF-8 log before importing Django.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import TextIO


ALLOWED_ROLES = {"migrate", "run_worker", "runserver", "check", "showmigrations"}


def _log_stream() -> tuple[TextIO | None, bool]:
    requested = os.environ.get("QB_INTERNAL_LOG", "").strip()
    if requested:
        path = Path(requested)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("a", encoding="utf-8", errors="backslashreplace", buffering=1), True
    if sys.stdout is not None:
        return sys.stdout, False

    import start_question_bank as launcher

    launcher.RUNTIME.mkdir(parents=True, exist_ok=True)
    return (launcher.RUNTIME / "internal.log").open(
        "a", encoding="utf-8", errors="backslashreplace", buffering=1
    ), True


def _run(role: str, extra: list[str]) -> int:
    if role == "_utf8_probe":
        print("处理试卷 中文文件名.pdf", flush=True)
        return 0
    if role not in ALLOWED_ROLES:
        raise ValueError(f"不支持的内部角色：{role}")

    import start_question_bank as launcher

    os.environ.update(launcher._child_environment(os.environ, keep_secrets=True))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")
    backend = str(launcher.BACKEND)
    if backend not in sys.path:
        sys.path.insert(0, backend)
    os.chdir(launcher.BACKEND)

    from django.core.management import execute_from_command_line

    execute_from_command_line(["manage.py", role, *extra])
    return 0


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if not arguments:
        raise ValueError("缺少内部角色")
    role, *extra = arguments
    stream, owned = _log_stream()
    assert stream is not None
    try:
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            return _run(role, extra)
    finally:
        if owned:
            stream.close()


if __name__ == "__main__":
    raise SystemExit(main())

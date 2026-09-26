#! python3.12
"""双击即以窗口版打开题库（不显示命令行窗口）。详细逻辑见 app_window.py。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

if len(sys.argv) >= 3 and sys.argv[1] == "--internal-role":
    import internal_runtime  # noqa: E402

    raise SystemExit(internal_runtime.main(sys.argv[2:]))

import app_window  # noqa: E402

raise SystemExit(app_window.main(sys.argv[1:]))

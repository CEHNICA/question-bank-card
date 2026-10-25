"""Cross-process check: the files the browser handed over really are on disk.

Reading a file in the same process that wrote it proves nothing — the write
buffer is still hot. This is run separately, from a different process, and the
Word file is opened as a zip so a truncated download cannot pass as a document.
"""

import os
import sys
import zipfile

BASE = (
    sys.argv[1]
    if len(sys.argv) > 1
    else os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "配图排版实测-1.24.8"
    )
)
for name in sorted(os.listdir(BASE)):
    path = os.path.join(BASE, name)
    extra = ""
    if name.endswith(".docx"):
        with zipfile.ZipFile(path) as archive:
            extra = f"  parts={len(archive.namelist())} document.xml={len(archive.read('word/document.xml'))} bytes"
    print(f"{name}: {os.path.getsize(path)} bytes{extra}")

"""Export the real questions through the running app and measure the result in WPS.

The synthetic document ``tools/measure_all_figure_rows.py`` builds is not what a
teacher downloads: it skips the whole export path. This asks the running app for
a real Word file for the questions whose figures wrap onto a second line, then
measures that file in WPS â€” the program the teacher actually opens it in.

Usage: python tools/export_real_and_measure.py [question-id ...]
"""
import json
import os
import sqlite3
import subprocess
import uuid
import sys
import urllib.request

BASE = "http://127.0.0.1:8768"
ROOT = r"C:\Users\17321\.minimax\sessions\mvs_fc946281665f4565a3d0ce7af01bd838\workspace\qbj-src"
DB = os.path.join(os.environ["LOCALAPPDATA"], "QuestionBankCard", "db.sqlite3")
OUT = os.path.join(os.environ["TEMP"], "qb-real-export.docx")

# å¿…ä¿®ä¸€å†Œ 1/2/4 wrap 2+1, its other ç¬¬ 1 é¢˜ wraps 2+2, and åˆä¸‰ç¬¬ 23 é¢˜ is the
# three-on-one-row case from the first report.
DEFAULT_IDS = [
    "1ef667e5-6d70-47f4-a9a1-35c0bbfb1e2f",
    "c9c3c9ce-a014-4256-b2d3-f4d71b9532de",
    "814b1e69-f5d7-4413-96dd-3f380e6d6bc6",
    "5f9f5a4e-270b-4fbab-b6ce-65a004d1450",
    "33de480e-99bc-4ced-b776-6812978246d8",
]


def units(value):
    return len(value.encode("utf-16-le")) // 2


def field(value):
    return {"source": value, "blocks": [{"type": "text", "start": 0, "end": units(value),
                                         "segments": [{"type": "text", "start": 0,
                                                       "end": units(value)}]}]} if value else []





def plain(value):
    """The database stores ids without dashes; the API takes them dashed."""
    return uuid.UUID(str(value).replace("-", ""))


def load(ids):
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rendered = {}
    for identifier in ids:
        row = connection.execute(
            "SELECT content FROM core_publishedquestion WHERE id = ?",
            (str(plain(identifier)).replace("-", ""),)).fetchone()
        if row is None:
            print("missing", identifier)
            continue
        content = json.loads(row["content"] or "{}")
        fields = {name: field(str(content.get(name) or ""))
                  for name in ("stem", "origin", "answer", "analysis")}
        fields.update({f"options.{key}": field(value)
                       for key, value in (content.get("options") or {}).items()})
        rendered[str(plain(identifier))] = fields
        stem = str(content.get("stem") or "")
        formulas = stem.count("$")
        print(f"  {identifier}  stem {len(stem)} chars, {formulas} dollar signs")
    return rendered


def main():
    ids = sys.argv[1:] or DEFAULT_IDS
    print("reading the real questions ...")
    rendered = load(ids)
    payload = {
        "ids": list(rendered),
        "title": "é…å›¾å±…ä¸­å¤éªŒ",
        "print_options": {"document": "questions", "student_info": False},
        "rendered_fields": rendered,
        "format": "docx",
    }
    request = urllib.request.Request(
        BASE + "/api/library/export-docx",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-QB-Request": "1"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            data = response.read()
            kind = response.headers.get("Content-Type")
    except urllib.error.HTTPError as error:
        print("export failed:", error.code, error.read().decode("utf-8")[:400])
        return 1
    with open(OUT, "wb") as handle:
        handle.write(data)
    print(f"exported {len(data)} bytes ({kind}) -> {OUT}")
    result = subprocess.run(
        [os.path.join(ROOT, "packaging", ".build", "venv", "Scripts", "python.exe"),
         os.path.join(ROOT, "tools", "measure_all_figure_rows_wps.py"), OUT],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(result.stdout or result.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())


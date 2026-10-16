"""Check that a teacher's figure layout survives the whole export path, for real.

The unit tests prove the formulas agree. This proves the app actually applies
them: it takes a real three-diagram question out of the running app, asks the
app for a Word file and a PDF with a layout the teacher set in the preview, then
opens the Word file in WPS — the program the teacher opens it in — turns it into
a PDF, and measures both papers in millimetres from the page edge.

The numbers to expect are written next to each check. A formula can be right and
still never reach the page; only measuring the page says whether it did.

Usage: python tools/check_figure_controls_real.py
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8768"
ROOT = r"C:\Users\17321\.minimax\sessions\mvs_fc946281665f4565a3d0ce7af01bd838\workspace\qbj-src"
OUT = os.path.join(ROOT, "配图排版实测")
DB = os.path.join(os.environ["LOCALAPPDATA"], "QuestionBankCard", "db.sqlite3")

# 初三「特殊的平行四边形」第 23 题：三块裁片，279×226 / 231×226 / 210×236 px。
# 在 150dpi 下是 47.2 / 39.1 / 35.6mm，三张挤一行是 129.3mm。
QUESTION = "33de480e-99bc-4ced-b776-6812978246d8"
# 老师在预览里做的事：把第一张放大到 100 毫米、整行靠左、点名第 3 张另起一行。
# 100 + 3.70 + 39.1 = 142.8mm 放得下，再加第三张就到 182.1mm 超出版心，所以
# 两边都该是「第一、二张一行，第三张一行」。
OVERRIDES = {"width": {"0": 100.0}, "align": "left", "breaks": [2]}
# 版心：页宽 210，两侧页边距 16，正文从 16.00mm 到 194.00mm。
LEFT_EDGE = 16.0
GAP_MM = 14 * 25.4 / 96
EXPECTED_WIDTH_MM = [100.0, 39.1, 35.6]
EXPECTED_LEFT_MM = [16.0, 16.0 + 100.0 + GAP_MM, 16.0]
EXPECTED_LINES = [2, 1]


def units(value):
    return len(value.encode("utf-16-le")) // 2


def field(value):
    if not value:
        return []
    return {"source": value, "blocks": [{"type": "text", "start": 0, "end": units(value),
                                         "segments": [{"type": "text", "start": 0, "end": units(value)}]}]}


def load():
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT content FROM core_publishedquestion WHERE id = ?",
                             (str(uuid.UUID(QUESTION)).replace("-", ""),)).fetchone()
    content = json.loads(row["content"] or "{}")
    figures = [figure for figure in content.get("figures") or [] if figure.get("slot") == "stem"]
    fields = {name: field(str(content.get(name) or "")) for name in ("stem", "origin", "answer", "analysis")}
    fields.update({f"options.{key}": field(value) for key, value in (content.get("options") or {}).items()})
    return content, figures, {QUESTION: fields}


def post(path, payload):
    request = urllib.request.Request(
        BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "X-QB-Request": "1"})
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        print(f"{path} 失败：{error.code} {error.read().decode('utf-8')[:400]}")
        raise SystemExit(1)


def render_with_wps(docx, pdf):
    """WPS is what the teacher opens the file in, so measure it in WPS."""
    import win32com.client
    app = win32com.client.Dispatch("KWPS.Application")
    app.Visible = False
    try:
        document = app.Documents.Open(os.path.abspath(docx), ReadOnly=True)
        try:
            document.ExportAsFixedFormat(os.path.abspath(pdf), 17)
        finally:
            document.Close(0)
    finally:
        try:
            app.Quit()
        except Exception:
            pass


def measure(pdf):
    """Every picture's box on the page, in millimetres from the page edge."""
    sys.path.insert(0, os.path.join(ROOT, "packaging", ".build", "venv", "Lib", "site-packages"))
    import fitz
    document = fitz.open(pdf)
    boxes = []
    for number, page in enumerate(document, start=1):
        point = 72 / 25.4
        for info in page.get_image_info():
            box = info["bbox"]
            boxes.append((number, round(box[0] / point, 1), round(box[2] / point, 1), round((box[2] - box[0]) / point, 1)))
    document.close()
    return boxes


def report(label, boxes):
    print(f"\n{label}")
    print(f"  {'页':>2}  {'左':>6}  {'右':>6}  {'宽':>6}   期望")
    for number, left, right, width in boxes:
        print(f"  {number:>2}  {left:>6.1f}  {right:>6.1f}  {width:>6.1f}")
    problems = []
    widths = [box[3] for box in boxes]
    lefts = [box[1] for box in boxes]
    if [round(value, 1) for value in widths] != EXPECTED_WIDTH_MM:
        problems.append(f"图宽 {widths}，期望 {EXPECTED_WIDTH_MM}")
    if [round(value, 1) for value in lefts] != [round(value, 1) for value in EXPECTED_LEFT_MM]:
        problems.append(f"左边缘 {lefts}，期望 {[round(v, 1) for v in EXPECTED_LEFT_MM]}")
    if problems:
        for text in problems:
            print("  [不一致] " + text)
    else:
        print("  [一致] 宽、位置、分行都与预览一致")
    return not problems


def main():
    os.makedirs(OUT, exist_ok=True)
    content, figures, rendered = load()
    print(f"第 23 题的题干配图：{[tuple(figure.get('bbox') or ()) for figure in figures]}")
    print(f"老师在预览里设的：{json.dumps(OVERRIDES, ensure_ascii=False)}")

    payload = {"ids": [QUESTION], "title": "配图排版实测",
               "print_options": {"document": "questions", "student_info": False,
                                 "figure_overrides": {QUESTION: OVERRIDES}},
               "rendered_fields": rendered, "format": "docx"}
    data, kind = post("/api/library/export-docx", payload)
    docx = os.path.join(OUT, "配图排版.docx")
    with open(docx, "wb") as handle:
        handle.write(data)
    print(f"\nWord：{len(data)} 字节 -> {docx}")

    rendered_pdf = os.path.join(OUT, "Word渲染.pdf")
    render_with_wps(docx, rendered_pdf)
    print(f"WPS 渲染 -> {rendered_pdf}")

    payload["format"] = "pdf"
    data, kind = post("/api/library/export-pdf", payload)
    pdf = os.path.join(OUT, "配图排版.pdf")
    with open(pdf, "wb") as handle:
        handle.write(data)
    print(f"PDF：{len(data)} 字节 -> {pdf}")

    word_ok = report("Word（经 WPS 渲染）", measure(rendered_pdf))
    pdf_ok = report("PDF（软件自己排的）", measure(pdf))
    print()
    print("预览 = Word = PDF" if word_ok and pdf_ok else "两边有一处对不上")
    return 0 if word_ok and pdf_ok else 1


if __name__ == "__main__":
    sys.exit(main())

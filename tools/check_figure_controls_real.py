"""Measure what the teacher's figure layout actually printed, in millimetres.

The export has to run in the page: KaTeX turns each formula into MathML there and
the server refuses to export un-converted LaTeX, so a script that reads the
database and posts its own payload is measuring a document the app would never
produce. This measures the two files the running app actually handed over.

The Word file is opened in WPS — the program the teacher opens it in — and turned
into a PDF first; reading the docx XML only proves this script wrote a number,
not that it printed. Both PDFs are then measured the same way and compared with
what the preview shows, which the browser measures separately.

Usage: python tools/check_figure_controls_real.py [folder]
"""
import os
import sys
import time

ROOT = r"C:\Users\17321\.minimax\sessions\mvs_fc946281665f4565a3d0ce7af01bd838\workspace\qbj-src"
# 输出放在仓库外面：这里面是真实试卷的内容，不进版本库。
FOLDER = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(ROOT), "配图排版实测-1.24.8")

# 版心：页宽 210，两侧页边距 16，正文从 16.00mm 到 194.00mm。
LEFT_EDGE = 16.0
GAP_MM = 14 * 25.4 / 96
# 老师在预览里设的是：第 1 张 100 毫米、整行靠左、第 3 张另起一行。
# 100 + 3.70 + 39.1 = 142.8mm 放得下，再加 35.6 就到 182.1mm 超出版心，所以
# 两边都该是「第一、二张一行，第三张一行」，两行都贴着左边距。
EXPECTED_WIDTH_MM = [100.0, 39.1, 35.6]
EXPECTED_LEFT_MM = [16.0, 16.0 + 100.0 + GAP_MM, 16.0]
EXPECTED_RIGHT_MM = [16.0 + 100.0, 16.0 + 100.0 + GAP_MM + 39.1, 16.0 + 35.6]
# 老师亲手填的那个数必须是准的：100 毫米就是 100 毫米，差半毫米都不叫一致。
CHOSEN_WIDTH_MM = 100.0
CHOSEN_TOLERANCE = 0.05
# 没动过的那几张，浏览器生成 PDF 时会把图的位置对齐到整磅（1 磅 = 0.35 毫米），
# 最多差 0.13 毫米。靠右的那一行在 WPS 里还会再差 0.4 毫米 —— 那是 WPS 摆一张
# 右对齐表格时自己的舍入，和排版规则无关：不调整任何东西导出的 Word 里，居中
# 那一行也是分毫不差的，所以这 0.4 毫米只在靠右时出现，量法必须认它。
BROWSER_TOLERANCE = 0.15
TABLE_ROUNDING_TOLERANCE = 0.5


def close(got, want, tolerance):
    return abs(got - want) <= tolerance


def worst(values, wanted):
    return max(abs(value - want) for value, want in zip(values, wanted))


def render_with_wps(docx, pdf):
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
    sys.path.insert(0, os.path.join(ROOT, "packaging", ".build", "venv", "Lib", "site-packages"))
    import fitz
    document = fitz.open(pdf)
    point = 72 / 25.4
    boxes = []
    for number, page in enumerate(document, start=1):
        for info in page.get_image_info():
            box = info["bbox"]
            boxes.append((number, round(box[0] / point, 1), round(box[2] / point, 1),
                          round((box[2] - box[0]) / point, 1)))
    document.close()
    return boxes


def report(label, boxes, preview=None):
    """Positions are judged on both ends; a row can start right and still end short."""
    print(f"\n{label}")
    print(f"  {'页':>2}  {'左(mm)':>7}  {'右(mm)':>7}  {'宽(mm)':>7}")
    for number, left, right, width in boxes:
        print(f"  {number:>2}  {left:>7.2f}  {right:>7.2f}  {width:>7.2f}")
    widths = [box[3] for box in boxes]
    lefts = [box[1] for box in boxes]
    rights = [box[2] for box in boxes]
    problems = []
    if not close(widths[0], CHOSEN_WIDTH_MM, CHOSEN_TOLERANCE):
        problems.append(f"老师填的 100 毫米印出来是 {widths[0]} 毫米")
    if worst(widths, EXPECTED_WIDTH_MM) > BROWSER_TOLERANCE:
        problems.append(f"图宽 {widths}，与预览的差超过 {BROWSER_TOLERANCE} 毫米")
    if worst(lefts, EXPECTED_LEFT_MM) > TABLE_ROUNDING_TOLERANCE:
        problems.append(f"左边缘 {lefts}，与预览的差超过 {TABLE_ROUNDING_TOLERANCE} 毫米")
    if worst(rights, EXPECTED_RIGHT_MM) > TABLE_ROUNDING_TOLERANCE:
        problems.append(f"右边缘 {rights}，与预览的差超过 {TABLE_ROUNDING_TOLERANCE} 毫米")
    print(f"  宽度最大差 {worst(widths, EXPECTED_WIDTH_MM):.2f} 毫米，"
          f"位置最大差 {max(worst(lefts, EXPECTED_LEFT_MM), worst(rights, EXPECTED_RIGHT_MM)):.2f} 毫米"
          f"（老师填的那个数差 {abs(widths[0] - CHOSEN_WIDTH_MM):.2f} 毫米）")
    for text in problems:
        print("  [不一致] " + text)
    if not problems:
        print("  [一致] 宽度、位置、分行都与预览一致")
    return not problems

def main():
    docx = os.path.join(FOLDER, "配图排版.docx")
    pdf = os.path.join(FOLDER, "配图排版.pdf")
    for path in (docx, pdf):
        if not os.path.isfile(path):
            print(f"缺少 {path}；先让运行中的软件导出这两个文件")
            return 1
        print(f"{os.path.basename(path)}：{os.path.getsize(path)} 字节")

    preview = ([100.0, 39.11, 35.56], [16.0, 119.7, 16.0])
    print(f"\n预览上量到的（浏览器报的）：宽 {preview[0]}  左 {preview[1]}")

    rendered = os.path.join(FOLDER, "Word渲染.pdf")
    render_with_wps(docx, rendered)
    print(f"WPS 渲染 -> {rendered}")

    word_ok = report("Word（经 WPS 渲染）", measure(rendered), preview)
    pdf_ok = report("PDF（软件自己排的）", measure(pdf), preview)
    print()
    print("预览 = Word = PDF" if word_ok and pdf_ok else "有一处对不上")
    return 0 if word_ok and pdf_ok else 1


if __name__ == "__main__":
    sys.exit(main())

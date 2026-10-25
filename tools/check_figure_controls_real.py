"""量老师在预览里调出来的配图，导出的 Word 和 PDF 到底印成了什么样。

排版必须跑在真页面里：公式是 KaTeX 在浏览器里转成 MathML 的，服务端拒收没转过的
LaTeX（400「公式尚未转换」）。所以「读库 + 自己拼一份提交上去」量的是软件根本不会
产出的文件。这里量的是运行中的软件亲手交出来的文件。

Word 那个要拿老师真正打开它的程序去量 —— 用 WPS 转成 PDF 再量。读 docx 里的数字只能
证明脚本写进去了，不能证明它印出来。两个 PDF 再用同一把尺子量，和预览比。

**两种排版是两条不同的路，必须分开量**，因为它们在 Word 里是两套写法：

- 图挤在一行放得下：Word 用一张表格装
- 一行放不下、每张各占一段：Word 用段落装

容差也按路分档，原因写在 WORD_TOLERANCE / PDF_TOLERANCE 那里。

用法：
    python tools/check_figure_controls_real.py              量「挤成一行」那一版
    python tools/check_figure_controls_real.py each-line    量「每张各占一段」那一版
    python tools/check_figure_controls_real.py baseline     量一份什么都没调的导出，
                                                             量出这条路的误差底线
    python tools/check_figure_controls_real.py <哪种> <文件夹>
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 输出放在仓库外面：这里面是真实试卷的内容，不进版本库。
FOLDER = os.path.join(os.path.dirname(ROOT), "配图排版实测-1.24.8")

# 版心：页宽 210，两侧页边距 16，正文从 16.00 毫米到 194.01 毫米。
GAP_MM = 14 * 25.4 / 96
RIGHT_EDGE = 194.0

# 每种排版：老师设了什么、预览上量到多少、该量哪两个文件。
# widths 是每张图该有多宽，edges 是每张图该在哪一段上，各按 (左, 右, 宽) 记。
CASES = {
    "one-row": {
        "docx": "配图排版.docx",
        "pdf": "配图排版.pdf",
        "shaped": "第一、二张挤在一行，第三张换到下一行，两行都贴着左边距",
        "instruction": "第 1 张 100 毫米、整行靠左、第 3 张另起一行。"
        "100 + 3.70 + 39.1 = 142.8 放得下，再加 35.6 就到 182.1 超出版心",
        "expected": [
            (16.0, 16.0 + 100.0, 100.0),
            (16.0 + 100.0 + GAP_MM, 16.0 + 100.0 + GAP_MM + 39.1, 39.1),
            (16.0, 16.0 + 35.6, 35.6),
        ],
        "chosen": [100.0, None, None],
    },
    "each-line": {
        "docx": "各占一行.docx",
        "pdf": "各占一行.pdf",
        "shaped": "三张图各占一行、都靠右；178 那张贴满版心，100 那两张右边缘同样顶到版心",
        "instruction": "依次 178 / 100 / 100 毫米、整行靠右。宽度加起来一行放不下，Word 于是每张各占一段，不再拼成表格",
        "expected": [
            (16.0, RIGHT_EDGE, 178.0),
            (RIGHT_EDGE - 100.0, RIGHT_EDGE, 100.0),
            (RIGHT_EDGE - 100.0, RIGHT_EDGE, 100.0),
        ],
        "chosen": [178.0, 100.0, 100.0],
    },
}
# 一张没调过的导出长什么样：这才是判断「差多少算错」的尺子。
# 150dpi 下第 23 题那三张理论上是 47.24 / 39.11 / 35.56 毫米。
BASELINE_WIDTH_MM = [47.24, 39.11, 35.56]

# 老师亲手填的毫米数必须印成多少就是多少，Word 全程 0.05 毫米。
WORD_TOLERANCE = 0.05
# 软件自己排的 PDF 走浏览器渲染，老师一个字都没改的图量出来宽度最大也差 0.12 毫米
# （实测 +0.12 / +0.05 / −0.11）—— 那是这条路上固有的底线，和排版规则无关。
# 所以这一档放到 0.15 毫米，不是放宽排版要求，是不拿量法的误差去冤枉排版。
# 改这两个数之前，先跑一次 baseline，看看底线还在不在。
PDF_TOLERANCE = 0.15


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
    """Every image box in the PDF, in millimetres from the top-left of the text area."""
    sys.path.insert(0, os.path.join(ROOT, "packaging", ".build", "venv", "Lib", "site-packages"))
    import fitz

    document = fitz.open(pdf)
    point = 72 / 25.4
    boxes = []
    for number, page in enumerate(document, start=1):
        for info in page.get_image_info():
            box = info["bbox"]
            boxes.append(
                (number, round(box[0] / point, 2), round(box[2] / point, 2), round((box[2] - box[0]) / point, 2))
            )
    document.close()
    return boxes


def print_boxes(boxes):
    print(f"  {'页':>2}  {'左(mm)':>7}  {'右(mm)':>7}  {'宽(mm)':>7}")
    for number, left, right, width in boxes:
        print(f"  {number:>2}  {left:>7.2f}  {right:>7.2f}  {width:>7.2f}")


def judge(label, boxes, case, tolerance):
    """Positions are judged on both ends; a row can start right and still end short."""
    print(f"\n{label}")
    print_boxes(boxes)
    expected = case["expected"]
    if len(boxes) != len(expected):
        print(f"  [不一致] 印出 {len(boxes)} 张图，该是 {len(expected)} 张")
        return False
    problems = []
    worst_width = worst_edge = 0.0
    for index, (box, want, chosen) in enumerate(zip(boxes, expected, case["chosen"]), start=1):
        worst_width = max(worst_width, abs(box[3] - want[2]))
        worst_edge = max(worst_edge, abs(box[1] - want[0]), abs(box[2] - want[1]))
        if chosen is not None and abs(box[3] - chosen) > tolerance:
            problems.append(f"第 {index} 张，老师填的 {chosen} 毫米印出来是 {box[3]} 毫米")
        if abs(box[1] - want[0]) > tolerance or abs(box[2] - want[1]) > tolerance:
            problems.append(f"第 {index} 张，位置 {box[1]}–{box[2]}，预览上是 {want[0]}–{want[1]}")
    print(f"  宽度最大差 {worst_width:.2f} 毫米，位置最大差 {worst_edge:.2f} 毫米（这一档容 {tolerance} 毫米）")
    for text in problems:
        print("  [不一致] " + text)
    if not problems:
        print(f"  [一致] {case['shaped']}，与预览一致")
    return not problems


def check_case(name):
    case = CASES[name]
    docx = os.path.join(FOLDER, case["docx"])
    pdf = os.path.join(FOLDER, case["pdf"])
    for path in (docx, pdf):
        if not os.path.isfile(path):
            print(f"缺少 {path}；先让运行中的软件导出这两个文件")
            return False
        print(f"{os.path.basename(path)}：{os.path.getsize(path)} 字节")

    print(f"\n老师在预览里设的：{case['instruction']}")
    print(f"预览上量到的：{case['shaped']}")
    rendered = os.path.join(FOLDER, os.path.splitext(case["docx"])[0] + "渲染.pdf")
    render_with_wps(docx, rendered)
    print(f"WPS 渲染 -> {os.path.basename(rendered)}")

    word_boxes = measure(rendered)
    pdf_boxes = measure(pdf)
    word_ok = judge("Word（经 WPS 渲染）", word_boxes, case, WORD_TOLERANCE)
    pdf_ok = judge("PDF（软件自己排的）", pdf_boxes, case, PDF_TOLERANCE)
    pages_ok = check_pages(word_boxes, pdf_boxes)
    print()
    print(f"预览 = Word = PDF（{case['shaped']}）" if word_ok and pdf_ok and pages_ok else "有一处对不上")
    return word_ok and pdf_ok and pages_ok


def check_pages(word_boxes, pdf_boxes):
    """图落在第几页也要一样：位置量得再准，被挤到下一页，印出来还是不一样。"""
    word_pages = [box[0] for box in word_boxes]
    pdf_pages = [box[0] for box in pdf_boxes]
    if word_pages == pdf_pages:
        print(f"\n分页也一致：图依次落在第 {'、'.join(str(page) for page in word_pages)} 页")
        return True
    print(f"\n[不一致] Word 的图依次在第 {word_pages} 页，PDF 的是第 {pdf_pages} 页")
    return False


def check_baseline():
    """Measure an export where the teacher changed nothing: that is the floor of the ruler."""
    pdf = os.path.join(FOLDER, "未调整.pdf")
    if not os.path.isfile(pdf):
        print(f"缺少 {pdf}；先让运行中的软件导出一份什么都没调的")
        return False
    print(f"{os.path.basename(pdf)}：{os.path.getsize(pdf)} 字节")
    print(
        "老师一个字都没改，图按原尺寸印出来，理论宽度："
        + " / ".join(f"{value:.2f}" for value in BASELINE_WIDTH_MM)
        + " 毫米\n"
    )
    boxes = measure(pdf)
    if len(boxes) < len(BASELINE_WIDTH_MM):
        print(f"只量到 {len(boxes)} 张图，这个文件不对")
        return False
    print_boxes(boxes[: len(BASELINE_WIDTH_MM)])
    worst = 0.0
    for index, (box, want) in enumerate(zip(boxes, BASELINE_WIDTH_MM), start=1):
        worst = max(worst, abs(box[3] - want))
        print(f"  第 {index} 张：量到 {box[3]:.2f} 毫米，理论 {want:.2f}，差 {box[3] - want:+.2f} 毫米")
    print(f"\n一个字没改，宽度最大也差 {worst:.2f} 毫米 —— 这就是这条路的底线。")
    print(f"PDF 那一档现在容 {PDF_TOLERANCE} 毫米。底线要是明显超过它，先查量法，再怀疑排版。")
    return worst <= PDF_TOLERANCE


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "one-row"
    if len(sys.argv) > 2:
        globals()["FOLDER"] = os.path.abspath(sys.argv[2])
    if which == "baseline":
        return 0 if check_baseline() else 1
    if which not in CASES:
        print(f"不知道要量哪一种。可选：{'、'.join(list(CASES) + ['baseline'])}")
        return 2
    return 0 if check_case(which) else 1


if __name__ == "__main__":
    sys.exit(main())

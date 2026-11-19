"""量 Word 里的**自然排版**位置。不要用 Word 导出的 PDF 量文字宽度。

背景（第六十八轮实测）
----------------------
Word 的 PDF 导出器（``ExportAsFixedFormat``）会把**字号和每个字宽**都量化到
1/120 英寸（0.6 磅）的网格上：

    请求 10.5 磅  ->  PDF 里是 10.8 磅（字号被量化）
    0.25em 空格    ->  自然 2.625 磅，PDF 里是 3.0 磅
    0.5em 下划线   ->  自然 5.25 磅，PDF 里是 5.4 磅

所以**从 Word 的 PDF 里量「这行有多宽」会偏 2.9%**，量出来的差不是文档的错，
是仪器的错。（当年就为这 3 毫米差点把字号改成 10 磅去迁就仪器。）

Word 自己的排版（屏幕上、打印时）是自然值。要量自然值，用 COM：

    Range.Information(7)   # wdHorizontalPositionRelativeToTextBoundary  ✅
    Range.Information(5)   # wdHorizontalPositionRelativeToPage         ❌ 居中段落返回页边距

COM 报的位置粒度是 1/120 英寸（0.6 磅），适合核对到 0.3 磅量级。

用法::

    python tools/word_layout_probe.py 导出.docx
    python tools/word_layout_probe.py 导出.docx --paragraph 2
    python tools/word_layout_probe.py 导出.docx --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WD_HPOS_REL_TEXT = 7  # wdHorizontalPositionRelativeToTextBoundary
WD_VPOS_REL_PAGE = 6  # wdVerticalPositionRelativeToPage
WD_PRINT_VIEW = 3


def probe(path: Path) -> list[dict]:
    """打开 docx，返回每段的自然排版几何。"""
    import win32com.client as win32

    word = win32.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    doc = word.Documents.Open(str(path), ReadOnly=True, AddToRecentFiles=False)
    try:
        try:
            word.ActiveWindow.View.Type = WD_PRINT_VIEW
        except Exception:  # pragma: no cover - 没有窗口时忽略
            pass
        left = float(doc.PageSetup.LeftMargin)
        rows: list[dict] = []
        for index in range(1, doc.Paragraphs.Count + 1):
            para = doc.Paragraphs(index).Range
            count = para.Characters.Count
            if count == 0:
                continue
            first = para.Characters(1)
            last = para.Characters(count)
            x_first = float(first.Information(WD_HPOS_REL_TEXT))
            x_last = float(last.Information(WD_HPOS_REL_TEXT))
            rows.append(
                {
                    "index": index,
                    "alignment": int(para.ParagraphFormat.Alignment),
                    "size": float(para.Font.Size),
                    "text": para.Text.replace("\r", ""),
                    "x_first": left + x_first,
                    "x_last": left + x_last,
                    "span": x_last - x_first,
                    "y": float(first.Information(WD_VPOS_REL_PAGE)),
                }
            )
        return rows
    finally:
        doc.Close(SaveChanges=0)
        word.Quit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", type=Path)
    parser.add_argument("--paragraph", type=int, default=None, help="只看某一段")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.docx.exists():
        print(f"找不到文件：{args.docx}", file=sys.stderr)
        return 2

    rows = probe(args.docx)
    if args.paragraph is not None:
        rows = [row for row in rows if row["index"] == args.paragraph]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print(f"自然排版几何（COM 粒度 0.6 磅）：{args.docx}")
    print(f"{'段':>4} {'字号':>6} {'对齐':>4} {'起点':>9} {'终点':>9} {'跨度':>9}  文本")
    for row in rows:
        print(
            f"{row['index']:>4} {row['size']:>6.1f} {row['alignment']:>4} "
            f"{row['x_first']:>9.2f} {row['x_last']:>9.2f} {row['span']:>9.2f}  "
            f"{row['text'][:48]!r}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

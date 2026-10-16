"""Measure every figure line in WPS, which is what the teacher actually opens in.

``tools/measure_word_pictures.ps1`` answers the same question for Microsoft Word.
The two do not necessarily agree: a Word measurement can call a row centred while
WPS lays that row out against the left margin, and the teacher only ever sees the
WPS one. Every "it is centred" claim has to be checked in WPS as well.

Usage: python tools/measure_all_figure_rows_wps.py [path-to-docx]
"""
import os
import sys

import win32com.client as win32

MM = 25.4 / 72.0
COLUMN = 5          # wdHorizontalPositionRelativeToPage
IN_TABLE = 12       # wdWithInTable


def cell_left_from_wps(cell, cell_mm, picture_mm):
    """Where a cell starts, from what WPS reports for it.

    WPS and Word disagree about ``Range.Information(5)`` on a table range. Word
    reports the table's left edge; WPS reports where the first cell's *text*
    starts, which for a centred paragraph is the middle of the cell. Adding the
    slack again (the obvious thing to do) double-counts it and makes every row
    look about half a gap too far right — which is how a correct document gets
    reported as broken. The slack is removed here instead, and the caller checks
    the result against a table whose position is known.
    """
    return cell.Range.Information(COLUMN) * MM - (cell_mm - picture_mm) / 2


def measure(path):
    app = win32.Dispatch("KWPS.Application")
    app.Visible = False
    try:
        doc = app.Documents.Open(path, False, True)
        setup = doc.PageSetup
        col_left = setup.LeftMargin * MM
        col_width = (setup.PageWidth - setup.LeftMargin - setup.RightMargin) * MM
        col_right = col_left + col_width
        print(f"text column {col_left:.2f} .. {col_right:.2f} mm  (width {col_width:.2f} mm)")
        print(f"page {setup.PageWidth * MM:.2f} x {setup.PageHeight * MM:.2f} mm")

        rows = []
        number = 0
        for table in doc.Tables:
            pictures = [shape for shape in table.Range.InlineShapes]
            if not pictures:
                continue
            number += 1
            first_row = table.Rows.Item(1)
            widths = [first_row.Cells.Item(index).Width * MM
                      for index in range(1, first_row.Cells.Count + 1)]
            picture_mm = [shape.Width * MM for shape in pictures]
            alignment = table.Rows.Alignment
            # Table edges come from the first and last cell, each measured the
            # way WPS reports it and then unwound to the cell's own edge.
            table_left = cell_left_from_wps(first_row.Cells.Item(1),
                                            widths[0], picture_mm[0])
            table_right = (cell_left_from_wps(first_row.Cells.Item(len(widths)),
                                             widths[-1], picture_mm[-1]) + widths[-1])
            spans = []
            before = 0.0
            for index, width in enumerate(widths):
                slack = (width - picture_mm[index]) / 2
                spans.append((table_left + before + slack,
                              table_left + before + slack + picture_mm[index]))
                before += width
            rows.append({
                "label": f"table row {number} ({len(pictures)} picture"
                         f"{'s' if len(pictures) > 1 else ''})",
                "left": min(item[0] for item in spans),
                "right": max(item[1] for item in spans),
                "align": alignment,
            })
            print(f"  table: edges {table_left:.2f} .. {table_right:.2f} mm "
                  f"({table_right - table_left:.2f}mm wide), {len(widths)} columns, "
                  f"rowsAlign={alignment}")

        loose = 0
        for shape in doc.InlineShapes:
            if shape.Range.Information(IN_TABLE):
                continue
            loose += 1
            width = shape.Width * MM
            alignment = int(shape.Range.Paragraphs.Item(1).Alignment)
            left = {1: col_left + (col_width - width) / 2,
                    2: col_right - width}.get(alignment, col_left)
            rows.append({"label": f"paragraph {loose} (alignment {alignment})",
                         "left": left, "right": left + width, "align": alignment})

        print()
        print("per line (WPS):")
        worst = 0.0
        off = 0
        for row in rows:
            skew = (row["left"] - col_left) - (col_right - row["right"])
            worst = max(worst, abs(skew))
            if abs(skew) > 0.5:
                off += 1
            verdict = "CENTRED" if abs(skew) <= 0.5 else "OFF CENTRE"
            print(f"  {row['label']:<30} left {row['left']:7.2f}  right {row['right']:7.2f}  "
                  f"content {row['right'] - row['left']:6.2f}mm  skew {skew:6.2f}  {verdict}")
        print()
        print(f"lines measured {len(rows)}, off centre {off}, worst skew {worst:.2f} mm")
        doc.Close(False)
    finally:
        try:
            app.Quit()
        except Exception:
            pass


if __name__ == "__main__":
    measure(sys.argv[1] if len(sys.argv) > 1
            else os.path.join(os.environ["TEMP"], "qb-all-figure-rows.docx"))

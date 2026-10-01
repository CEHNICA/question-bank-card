"""Tables that are only words and numbers are question text, not pictures.

A printed table (a contingency table to fill in, a data table, a
distribution) is written into the stem as a Markdown pipe table::

    | | 优级品 | 非优级品 |
    |---|---|---|
    | 甲车间 | | |
    | 乙车间 | | |

A table with merged cells, which Markdown cannot express, is kept as a small
HTML ``<table>`` (``tr``/``td``/``th`` with ``rowspan``/``colspan`` only).

MinerU recognises printed tables itself and returns them as HTML; it also
joins a table that a page break cut in two.  That HTML is kept on the block
(``Block.html``) so a table figure can be turned into text, and so the text
table a reader wrote can be checked against an independent engine.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import zipfile
from pathlib import Path

MAX_TABLE_HTML = 20000
MAX_ROWS = 60
MAX_COLS = 20

_SEPARATOR_CELL = re.compile(r"^\s*:?-{3,}:?\s*$")
_HTML_TABLE = re.compile(r"<table\b[^>]*>[\s\S]*?</table\s*>", re.IGNORECASE)
_ROW = re.compile(r"<tr\b[^>]*>([\s\S]*?)</tr\s*>", re.IGNORECASE)
_CELL = re.compile(r"<(td|th)\b([^>]*)>([\s\S]*?)</\1\s*>", re.IGNORECASE)
_SPAN = re.compile(r"\b(rowspan|colspan)\s*=\s*[\"']?(\d{1,2})", re.IGNORECASE)
_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------- 读 Markdown 表


def split_cells(line: str) -> list[str]:
    """Split one ``| a | b |`` row.  A ``|`` inside ``$…$`` (|x|) or escaped
    as ``\\|`` belongs to the cell."""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    cells, current, in_math, index = [], [], False, 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] == "|" and not in_math:
            current.append("|")
            index += 2
            continue
        if char == "$":
            in_math = not in_math
        if char == "|" and not in_math:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def markdown_tables(text: str) -> list[dict]:
    """Every pipe table in ``text``: ``{"start", "end", "rows", "header"}``.

    Two or more consecutive lines that start with ``|`` make a table; a
    ``|---|`` line after the first row marks that row as the header.  A single
    line such as ``|x|=2`` is not a table.
    """
    tables = []
    lines = str(text or "").split("\n")
    offset = 0
    starts = []
    for line in lines:
        starts.append(offset)
        offset += len(line) + 1
    index = 0
    while index < len(lines):
        if not _is_table_line(lines[index]):
            index += 1
            continue
        end = index
        while end + 1 < len(lines) and _is_table_line(lines[end + 1]):
            end += 1
        if end > index:
            rows = [split_cells(line) for line in lines[index:end + 1]]
            header = len(rows) > 1 and all(_SEPARATOR_CELL.match(cell) for cell in rows[1] if cell) \
                and any(rows[1])
            if header:
                rows = [rows[0], *rows[2:]]
            tables.append({
                "start": starts[index],
                "end": starts[end] + len(lines[end]),
                "rows": rows,
                "header": header,
            })
        index = end + 1
    return tables


def html_tables(text: str) -> list[dict]:
    return [{"start": match.start(), "end": match.end(), "rows": [
        [cell["text"] for cell in row] for row in parse_html(match.group(0))
    ]} for match in _HTML_TABLE.finditer(str(text or ""))]


def all_tables(text: str) -> list[dict]:
    return sorted([*markdown_tables(text), *html_tables(text)], key=lambda item: item["start"])


def has_table(text: str) -> bool:
    return bool(all_tables(text))


def without_tables(text: str) -> str:
    """The question text with its tables taken out (for comparing prose)."""
    value = str(text or "")
    for table in reversed(all_tables(value)):
        value = value[:table["start"]] + value[table["end"]:]
    return re.sub(r"\n{3,}", "\n\n", value)


def cell_texts(text: str) -> list[str]:
    """All cell texts of all tables, in reading order."""
    return [cell for table in all_tables(text) for row in table["rows"] for cell in row]


# ---------------------------------------------------------------- 读 HTML 表（MinerU）


def _cell_text(raw: str) -> str:
    value = _BREAK.sub(" ", raw)
    value = _TAG.sub("", value)
    value = html_lib.unescape(value).replace(" ", " ")
    return re.sub(r"\s+", " ", value).strip()


def parse_html(value: str) -> list[list[dict]]:
    """Rows of ``{"text", "rowspan", "colspan", "header"}`` from a table's HTML."""
    rows = []
    for row_match in _ROW.finditer(str(value or "")[:MAX_TABLE_HTML]):
        cells = []
        for cell_match in _CELL.finditer(row_match.group(1)):
            spans = {name.lower(): int(number) for name, number in _SPAN.findall(cell_match.group(2))}
            cells.append({
                "text": _cell_text(cell_match.group(3)),
                "rowspan": max(1, min(MAX_ROWS, spans.get("rowspan", 1))),
                "colspan": max(1, min(MAX_COLS, spans.get("colspan", 1))),
                "header": cell_match.group(1).lower() == "th",
            })
        if cells:
            rows.append(cells[:MAX_COLS])
        if len(rows) >= MAX_ROWS:
            break
    return rows


def _markdown_cell(text: str) -> str:
    """A cell for a pipe table: a ``|`` outside maths is escaped."""
    parts = re.split(r"(\$[^$]*\$)", text)
    return "".join(part if part.startswith("$") else part.replace("|", "\\|") for part in parts)


def to_text(value: str) -> str:
    """MinerU's table HTML as question text: Markdown when it can say it,
    otherwise a clean HTML table."""
    rows = parse_html(value)
    if not rows:
        return ""
    merged = any(cell["rowspan"] > 1 or cell["colspan"] > 1 for row in rows for cell in row)
    if merged:
        body = []
        for row in rows:
            cells = []
            for cell in row:
                tag = "th" if cell["header"] else "td"
                attrs = "".join(
                    f' {name}="{cell[name]}"' for name in ("rowspan", "colspan") if cell[name] > 1
                )
                cells.append(f"<{tag}{attrs}>{html_lib.escape(cell['text'], quote=False)}</{tag}>")
            body.append(f"<tr>{''.join(cells)}</tr>")
        return f"<table>{''.join(body)}</table>"
    width = max(len(row) for row in rows)
    lines = []
    for index, row in enumerate(rows):
        cells = [_markdown_cell(cell["text"]) for cell in row] + [""] * (width - len(row))
        lines.append("| " + " | ".join(cells) + " |")
        if index == 0:
            lines.append("|" + "---|" * width)
    return "\n".join(lines)


# ---------------------------------------------------------------- MinerU 解析包里的表


def archive_tables(archive: Path) -> list[dict]:
    """``{"page_idx", "bbox", "html"}`` for each table MinerU recognised."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            names = [n for n in bundle.namelist()
                     if n.endswith("_content_list.json") and not n.endswith("_content_list_v2.json")]
            if len(names) != 1:
                return []
            content = json.loads(bundle.read(names[0]))
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        return []
    result = []
    for raw in content if isinstance(content, list) else []:
        if not isinstance(raw, dict) or raw.get("type") != "table":
            continue
        body = raw.get("table_body")
        bbox = raw.get("bbox")
        if isinstance(body, str) and body.strip() and isinstance(raw.get("page_idx"), int) \
                and isinstance(bbox, list) and len(bbox) == 4:
            result.append({"page_idx": raw["page_idx"], "bbox": [float(v) for v in bbox],
                           "html": body[:MAX_TABLE_HTML]})
    return result


def _same_box(a: list, b: list, tolerance: float = 1.5) -> bool:
    return all(abs(float(x) - float(y)) <= tolerance for x, y in zip(a, b))


_CHECKED: set = set()


def table_blocks(paper) -> list[dict]:
    """Table blocks with their HTML, in reading order.  Papers parsed before
    the HTML was kept get it from the saved MinerU archive, once."""
    from .models import Block
    from .pipeline import paper_dir

    blocks = list(Block.objects.filter(paper=paper, type="table").exclude(bbox=None).order_by("seq"))
    if any(not block.html for block in blocks) and paper.pk not in _CHECKED:
        archive = Path(paper.zip_path) if getattr(paper, "zip_path", "") else None
        if archive is None or not archive.is_file():
            archive = paper_dir(paper) / "mineru_result.zip"
        found = archive_tables(archive) if archive.is_file() else []
        for block in blocks:
            if block.html:
                continue
            match = next((item for item in found if item["page_idx"] == block.page_idx
                          and _same_box(item["bbox"], block.bbox)), None)
            if match:
                block.html = match["html"]
                block.save(update_fields=["html"])
        # A piece a page break cut off has no HTML of its own; look once per run.
        _CHECKED.add(paper.pk)
    return [{"page_idx": block.page_idx, "bbox": block.bbox, "html": block.html, "seq": block.seq}
            for block in blocks]


def continuations(block: dict, blocks: list[dict]) -> list[dict]:
    """The pieces of a table a page break cut off.  MinerU puts every row into
    the first piece and leaves the following pieces without HTML."""
    result = []
    later = [item for item in blocks if item["seq"] > block["seq"]]
    for item in later:
        if item["html"] or item["page_idx"] not in {block["page_idx"], block["page_idx"] + 1}:
            break
        result.append(item)
    return result


def covers(block: dict, piece: dict, threshold: float = 0.6) -> bool:
    return block["page_idx"] == piece.get("page_idx") and _overlap(block["bbox"], piece.get("bbox") or []) >= threshold


def insert_table(stem: str, table: str, before: list[str], key=lambda value: value) -> str:
    """Put ``table`` on its own lines after the stem line that ends the text
    printed just before it (``before``: that text, nearest first).  Without a
    match the table goes at the end."""
    lines = str(stem or "").split("\n")
    occupied = set()
    offset = 0
    spans = all_tables(stem)
    for index, line in enumerate(lines):
        if any(span["start"] <= offset <= span["end"] for span in spans):
            occupied.add(index)
        offset += len(line) + 1
    for text in before:
        wanted = key(text)
        if len(wanted) < 2:
            continue
        tail = wanted[-min(8, len(wanted)):]
        for index, line in enumerate(lines):
            if index not in occupied and tail in key(line):
                return "\n".join([*lines[:index + 1], table, *lines[index + 1:]])
    return "\n".join([*lines, table]).strip("\n") if str(stem or "").strip() else table


def table_for_figure(paper, figure: dict, blocks: list[dict] | None = None) -> dict | None:
    """The MinerU table (with HTML) behind a figure or one of its pieces.  A
    piece a page break cut off leads back to the first piece of its table."""
    blocks = table_blocks(paper) if blocks is None else blocks
    pieces = [figure, *[part for part in (figure.get("parts") or []) if isinstance(part, dict)]]
    for piece in pieces:
        for block in blocks:
            if not covers(block, piece):
                continue
            if block["html"]:
                return block
            head = next((item for item in reversed(blocks) if item["seq"] < block["seq"] and item["html"]
                         and block in continuations(item, blocks)), None)
            if head:
                return head
    return None


def _overlap(a: list, b: list) -> float:
    if len(a) != 4 or len(b) != 4:
        return 0.0
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / smaller if smaller > 0 else 0.0

"""Word (.docx / .doc) dual-track conversion and zero-Office evidence rendering."""

from __future__ import annotations

import html as html_lib
import re
import zipfile
from pathlib import Path
from typing import Any

from . import native_pdf, printed_answers, segment

COM_PROG_IDS: tuple[tuple[str, str], ...] = (
    ("Word.Application", "word"),
    ("Kwps.Application", "wps"),
    ("WPS.Application", "wps"),
)


class WordConversionError(RuntimeError):
    """Raised when a legacy .doc or corrupt Word file cannot be converted locally."""


def is_docx_zip(path: Path) -> bool:
    try:
        if not path.is_file() or not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path, "r") as zf:
            return "word/document.xml" in zf.namelist()
    except OSError:
        return False


def _com_convert(source: Path, *, pdf_target: Path | None = None, docx_target: Path | None = None) -> str:
    """Convert Word document via Microsoft Word or WPS Writer COM in a single session.

    Returns the application label ('word' or 'wps') on success, or raises RuntimeError.
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError("此电脑没有可用的 COM 转换组件") from exc

    last_error: Exception | None = None
    for prog_id, label in COM_PROG_IDS:
        pythoncom.CoInitialize()
        app = None
        document = None
        try:
            app = win32com.client.DispatchEx(prog_id)
            try:
                app.Visible = False
            except Exception:
                pass
            try:
                app.DisplayAlerts = 0
            except Exception:
                pass
            try:
                app.AutomationSecurity = 3
            except Exception:
                pass
            document = app.Documents.Open(
                FileName=str(source.resolve()),
                ConfirmConversions=False,
                ReadOnly=True,
                AddToRecentFiles=False,
                Visible=False,
            )
            if docx_target is not None:
                docx_target.parent.mkdir(parents=True, exist_ok=True)
                saved = False
                for fmt in (16, 12):
                    for method_name in ("SaveAs2", "SaveAs"):
                        method = getattr(document, method_name, None)
                        if method is None:
                            continue
                        try:
                            method(FileName=str(docx_target.resolve()), FileFormat=fmt)
                            if docx_target.is_file() and docx_target.stat().st_size > 0:
                                saved = True
                                break
                        except Exception:
                            continue
                    if saved:
                        break
                if not saved:
                    raise RuntimeError(f"{label} 未能将文档另存为 DOCX")
            if pdf_target is not None:
                pdf_target.parent.mkdir(parents=True, exist_ok=True)
                exported = False
                export_fn = getattr(document, "ExportAsFixedFormat", None)
                if export_fn is not None:
                    try:
                        export_fn(OutputFileName=str(pdf_target.resolve()), ExportFormat=17)
                        exported = pdf_target.is_file() and pdf_target.stat().st_size > 0
                    except Exception:
                        exported = False
                if not exported:
                    for method_name in ("SaveAs2", "SaveAs"):
                        method = getattr(document, method_name, None)
                        if method is not None:
                            try:
                                method(FileName=str(pdf_target.resolve()), FileFormat=17)
                                if pdf_target.is_file() and pdf_target.stat().st_size > 0:
                                    exported = True
                                    break
                            except Exception:
                                continue
                if not exported:
                    raise RuntimeError(f"{label} 未能导出 PDF 预览")
            return label
        except Exception as exc:
            last_error = exc
        finally:
            if document is not None:
                try:
                    document.Close(SaveChanges=False)
                except Exception:
                    pass
            if app is not None:
                try:
                    app.Quit(SaveChanges=False)
                except Exception:
                    pass
            pythoncom.CoUninitialize()

    raise RuntimeError("当前电脑未安装可用的 Microsoft Word 或 WPS 文字") from last_error


def _block_to_html(block: dict[str, Any], archive_files: dict[str, bytes]) -> str:
    text = str(block.get("text") or "")
    imgs = list(block.get("images") or [])
    img_tags: list[str] = []
    for img in imgs:
        raw = img.get("bytes")
        if isinstance(raw, bytes) and raw:
            name = f"img_{img.get('sha256', '')[:16]}_{len(archive_files)}.png"
            archive_files[name] = raw
            w = min(420, max(36, int(img.get("width", 160) * 0.65)))
            img_tags.append(f'<div style="margin:4pt 0;"><img src="{name}" width="{w}"></div>')

    if block.get("is_table") and not block.get("option_cells"):
        if text.startswith("<table"):
            body_html = text
        else:
            rows = block.get("table_rows") or []
            trs = []
            for row in rows:
                tds = "".join(
                    f'<td style="border:0.6pt solid #334155;padding:3pt 6pt;">{html_lib.escape(str(c))}</td>'
                    for c in row
                )
                trs.append(f"<tr>{tds}</tr>")
            body_html = (
                f'<table style="border-collapse:collapse;margin:4pt 0;font-size:10.5pt;">{"".join(trs)}</table>'
                if trs
                else ""
            )
        return body_html + "".join(img_tags)

    escaped = html_lib.escape(text).replace("\t", "&nbsp;&nbsp;&nbsp;&nbsp;").replace("\n", "<br>")
    if not escaped.strip() and not img_tags:
        return ""
    return f'<p style="margin:3pt 0;line-height:1.45;">{escaped}</p>' + "".join(img_tags)


def render_docx_fallback_pdf(source: Path, target: Path, *, extracted: dict[str, Any] | None = None) -> None:
    """Zero-Office fallback: render DOCX blocks + embedded images into a clean A4 PDF via PyMuPDF Story."""
    import pymupdf as fitz

    from . import word_extract

    data = extracted if extracted is not None else word_extract.extract_docx(source)
    blocks = data.get("all_blocks") or []
    archive_files: dict[str, bytes] = {}
    html_parts = [_block_to_html(b, archive_files) for b in blocks]
    body_inner = "\n".join(p for p in html_parts if p) or "<p>（空白文档）</p>"

    font_css = "font-family: sans-serif;"
    archive = fitz.Archive()
    for fname, fbytes in archive_files.items():
        archive.add(fbytes, fname)
    for win_font in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simsun.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ):
        if win_font.is_file():
            try:
                archive.add(win_font)
                font_css = f'@font-face {{ font-family: "DocFallbackCJK"; src: url({win_font.name}); }} font-family: "DocFallbackCJK", sans-serif;'
                break
            except Exception:
                continue

    html_doc = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>body {{ {font_css} font-size: 10.5pt; color: #0f172a; }} "
        "table { width: auto; } td { vertical-align: middle; }</style></head>"
        f"<body>{body_inner}</body></html>"
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    mediabox = fitz.paper_rect("a4")
    where = mediabox + (42, 42, -42, -42)
    story = fitz.Story(html=html_doc, archive=archive)
    writer = fitz.DocumentWriter(str(target))
    try:
        more = True
        page_guard = 0
        while more and page_guard < 500:
            page_guard += 1
            device = writer.begin_page(mediabox)
            more, _ = story.place(where)
            story.draw(device)
            writer.end_page()
    finally:
        writer.close()


def convert_docx_to_pdf(source: Path, target: Path) -> None:
    """Convert .docx / .doc to PDF via Word/WPS COM, falling back to local HTML-PDF renderer for .docx."""
    try:
        _com_convert(source, pdf_target=target)
        return
    except Exception as com_exc:
        if is_docx_zip(source):
            render_docx_fallback_pdf(source, target)
            return
        suffix = source.suffix.lower()
        if suffix == ".doc":
            raise WordConversionError(
                "老版 .doc 文件需要本机安装 Microsoft Word 或 WPS 才能转换；当前电脑未检测到可用办公软件，请先另存为 .docx 或 PDF 再上传。"
            ) from com_exc
        raise WordConversionError("这份 Word 文件损坏或格式不受支持，请检查文件后重试。") from com_exc


def prepare_word_sources(source: Path, folder: Path, *, pdf_target: Path | None = None) -> dict[str, Any]:
    """Prepare both tracks for a .docx / .doc upload:

    Returns ``{"docx_path": Path, "pdf_path": Path, "evidence_source": "office_com" | "html_fallback", "evidence_app": str, "extracted": dict}``.
    """
    from . import word_extract

    pdf_out = pdf_target if pdf_target is not None else (folder / "converted.pdf")
    if is_docx_zip(source):
        extracted = word_extract.extract_docx(source)
        try:
            app_label = _com_convert(source, pdf_target=pdf_out)
            return {
                "docx_path": source,
                "pdf_path": pdf_out,
                "evidence_source": "office_com",
                "evidence_app": app_label,
                "extracted": extracted,
            }
        except Exception:
            render_docx_fallback_pdf(source, pdf_out, extracted=extracted)
            return {
                "docx_path": source,
                "pdf_path": pdf_out,
                "evidence_source": "html_fallback",
                "evidence_app": "pymupdf_story",
                "extracted": extracted,
            }

    # Legacy .doc (or non-zip Word file): convert to .docx + .pdf in one Office/WPS COM pass
    docx_out = folder / "converted_source.docx"
    try:
        app_label = _com_convert(source, pdf_target=pdf_out, docx_target=docx_out)
    except Exception as exc:
        if source.suffix.lower() == ".doc":
            raise WordConversionError(
                "老版 .doc 文件需要本机安装 Microsoft Word 或 WPS 才能自动转换；当前电脑未检测到可用办公软件，请先另存为 .docx 或 PDF 再上传。"
            ) from exc
        raise WordConversionError("这份 Word 文件无法打开（可能不是有效的 DOCX 文档），请检查文件后重试。") from exc

    extracted = word_extract.extract_docx(docx_out)
    return {
        "docx_path": docx_out,
        "pdf_path": pdf_out,
        "evidence_source": "office_com",
        "evidence_app": app_label,
        "extracted": extracted,
    }


def _valid_region_list(regions: list[dict[str, Any]] | None, page_count: int) -> list[dict[str, Any]]:
    if not regions:
        return []
    cleaned: list[dict[str, Any]] = []
    for item in regions:
        p = item.get("page_idx")
        box = item.get("bbox")
        if (
            type(p) is int
            and 0 <= p < page_count
            and isinstance(box, list)
            and len(box) == 4
            and box[2] - box[0] >= 4
            and box[3] - box[1] >= 4
        ):
            cleaned.append(
                {
                    "page_idx": p,
                    "bbox": [
                        round(max(0.0, min(1000.0, float(box[0]))), 1),
                        round(max(0.0, min(1000.0, float(box[1]))), 1),
                        round(max(0.0, min(1000.0, float(box[2]))), 1),
                        round(max(0.0, min(1000.0, float(box[3]))), 1),
                    ],
                }
            )
    return cleaned


def map_word_questions_to_pdf(
    questions: list[dict[str, Any]], pdf_path: Path, paper_pages: list[dict[str, Any]]
) -> dict[str, Any]:
    """Extract PDF blocks from converted.pdf and map each Word question onto PDF regions & visual boxes."""
    pdf_data = native_pdf.extract(pdf_path)
    blocks: list[dict[str, Any]] = pdf_data.get("blocks") or []
    pages_meta: list[dict[str, Any]] = pdf_data.get("pages") or []
    page_count = max(1, len(paper_pages))

    # Find answer chapter boundary on the PDF so answer numbers on back pages don't pollute question regions
    exam_blocks: list[dict[str, Any]] = []
    answer_boundary: tuple[int, float] | None = None
    for b in blocks:
        if b.get("type") == "text" and printed_answers.is_answer_chapter_heading(str(b.get("text") or "")):
            bbox = b.get("bbox") or [0, 0, 1000, 1000]
            answer_boundary = (int(b["page_idx"]), float(bbox[1]))
            break
    if answer_boundary is not None:
        b_page, b_y = answer_boundary
        for b in blocks:
            p = int(b.get("page_idx", 0))
            by = float((b.get("bbox") or [0, 0, 0, 0])[1])
            if p < b_page or (p == b_page and by < b_y - 2):
                exam_blocks.append(b)
    else:
        exam_blocks = list(blocks)

    exam_last_page = max((int(b["page_idx"]) for b in exam_blocks), default=page_count - 1)

    # Try segment.segment when single continuous group; also build direct line-anchor index as fallback
    seg_by_num: dict[int, dict[str, Any]] = {}
    distinct_groups = {q["group_idx"] for q in questions}
    if len(distinct_groups) <= 1 and exam_blocks:
        try:
            seg_res = segment.segment(paper_pages[: exam_last_page + 1], exam_blocks)
            for sq in seg_res.get("questions") or []:
                seg_by_num.setdefault(int(sq["number"]), sq)
        except Exception:
            seg_by_num = {}

    # Also build ordered number-line starts in exam_blocks for multi-group or unmatched questions
    num_starts: list[tuple[int, int, float, int]] = []
    num_line_re = re.compile(r"^\s*[*※▲]?\s*(\d{1,3})\s*[.．、]")
    for b in exam_blocks:
        if b.get("type") != "text":
            continue
        m = num_line_re.match(str(b.get("text") or ""))
        if m and b.get("bbox"):
            num_starts.append((int(m.group(1)), int(b["page_idx"]), float(b["bbox"][1]), int(b["seq"])))

    visual_blocks = [b for b in exam_blocks if b.get("type") in {"image", "table", "chart"} and b.get("bbox")]

    total_q = max(1, len(questions))
    cursor_start_idx = 0
    mapped_list: list[dict[str, Any]] = []

    for idx, q in enumerate(questions):
        q_num = int(q["number"])
        regions: list[dict[str, Any]] = []
        anchor_seq: int | None = None

        if len(distinct_groups) <= 1 and q_num in seg_by_num:
            sq = seg_by_num[q_num]
            regions = _valid_region_list(sq.get("regions"), page_count)
            anchor_seq = sq.get("source_anchor_seq")

        if not regions:
            matched_pos: int | None = None
            for pos in range(cursor_start_idx, len(num_starts)):
                if num_starts[pos][0] == q_num:
                    matched_pos = pos
                    break
            if matched_pos is not None:
                cursor_start_idx = matched_pos + 1
                _n, st_page, st_y, anchor_seq = num_starts[matched_pos]
                y0 = max(0.0, st_y - 8.0)
                if matched_pos + 1 < len(num_starts):
                    _nn, nx_page, nx_y, _nseq = num_starts[matched_pos + 1]
                    if nx_page == st_page and nx_y > y0 + 12.0:
                        regions = _valid_region_list(
                            [{"page_idx": st_page, "bbox": [35.0, y0, 965.0, nx_y - 2.0]}], page_count
                        )
                    elif nx_page > st_page:
                        spans = [{"page_idx": st_page, "bbox": [35.0, y0, 965.0, 960.0]}]
                        for mid_p in range(st_page + 1, min(nx_page, st_page + 3)):
                            spans.append({"page_idx": mid_p, "bbox": [35.0, 40.0, 965.0, 960.0]})
                        if nx_y > 55.0:
                            spans.append({"page_idx": nx_page, "bbox": [35.0, 40.0, 965.0, nx_y - 2.0]})
                        regions = _valid_region_list(spans, page_count)
                else:
                    regions = _valid_region_list([{"page_idx": st_page, "bbox": [35.0, y0, 965.0, 960.0]}], page_count)

        if not regions:
            # Proportional fallback so regions is always valid for preview & cropping
            est_page = min(exam_last_page, int(idx * (exam_last_page + 1) / total_q))
            on_same_page = [
                i for i in range(total_q) if min(exam_last_page, int(i * (exam_last_page + 1) / total_q)) == est_page
            ]
            local_i = on_same_page.index(idx) if idx in on_same_page else 0
            local_n = max(1, len(on_same_page))
            band_h = 880.0 / local_n
            y0 = round(50.0 + local_i * band_h, 1)
            y1 = round(min(960.0, y0 + max(40.0, band_h - 4.0)), 1)
            regions = [{"page_idx": est_page, "bbox": [35.0, y0, 965.0, y1]}]

        # Collect visual boxes inside this question's regions
        in_region_visuals: list[dict[str, Any]] = []
        for vb in visual_blocks:
            vp = int(vb["page_idx"])
            vbx = vb["bbox"]
            vcx = (float(vbx[0]) + float(vbx[2])) / 2.0
            vcy = (float(vbx[1]) + float(vbx[3])) / 2.0
            if any(
                r["page_idx"] == vp
                and r["bbox"][0] - 15 <= vcx <= r["bbox"][2] + 15
                and r["bbox"][1] - 15 <= vcy <= r["bbox"][3] + 15
                for r in regions
            ):
                in_region_visuals.append(vb)

        candidates = [
            {"label": str(c_idx + 1), "page_idx": int(vb["page_idx"]), "bbox": list(vb["bbox"]), "seq": vb["seq"]}
            for c_idx, vb in enumerate(in_region_visuals[:20])
        ]
        mapped_list.append({"regions": regions, "anchor_seq": anchor_seq, "candidates": candidates})

    return {"blocks": blocks, "pages": pages_meta, "mapped": mapped_list}

"""Direct lossless extraction of questions, formulas, tables, images, and answers from DOCX."""

from __future__ import annotations

import hashlib
import html as html_lib
import io
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image

from . import printed_answers, prose, qtypes, textnorm
from .word_mtef import ole_equation_to_latex
from .word_omml import OMML_NS, WORD_NS, omml_to_latex

REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DRAWING_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
VML_NS = "urn:schemas-microsoft-com:vml"
OFFICE_O_NS = "urn:schemas-microsoft-com:office:office"

FLAG_OLE_EQUATION_FALLBACK = "含未能自动转成可编辑公式的旧版公式对象，已保留原公式图，请对照原卷核对"
FLAG_VECTOR_DRAWING = "检测到 Word 线条或矢量绘图，未内嵌为独立图片；请对照左侧原卷点“配图”框选补图，或确认无图"
FLAG_VECTOR_IMAGE_UNREADABLE = "含无法直接转码的矢量图元（WMF/EMF），请对照左侧原卷点“配图”框选补图"
FLAG_ANSWER_MISMATCH_PREFIX = "卷末参考答案核对提醒："

_SYM_CHAR_MAP: dict[str, str] = {
    "F044": "△",
    "F0D0": "∠",
    "F05E": "⊥",
    "F0B0": "°",
    "F0B1": "±",
    "F0B4": "×",
    "F0B8": "÷",
    "F0A3": "≤",
    "F0B3": "≥",
    "F0B9": "≠",
    "F0BB": "≈",
    "F0BA": "≡",
    "F0A5": "∞",
    "F0CE": "∈",
    "F0CF": "∉",
    "F0CC": "⊂",
    "F0CD": "⊆",
    "F0C8": "∪",
    "F0C7": "∩",
    "F0C6": "∅",
    "F0AE": "→",
    "F0DE": "⇒",
    "F0DB": "⇔",
    "F070": "π",
    "F061": "α",
    "F062": "β",
    "F067": "γ",
    "F071": "θ",
    "F06C": "λ",
    "F077": "ω",
}

_CN_DIGITS = ("零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十")
_NOTICE_HEADER_RE = re.compile(r"^\s*(?:注意事项|考生须知|答题须知|考试须知|答卷须知)\s*[：:]?")
_INSTRUCTION_RE = re.compile(
    r"答题前|答卷前|答题卡|答题纸|准考证|考生号|考籍号|条形码|考试结束|签字笔|2B\s*铅笔|"
    r"试题卷|试卷上|本试卷|考试时间|草稿纸|选涂|涂黑|作答无效|答题无效|交回"
)
_SEAL_LINE_RE = re.compile(r"密\s*封\s*线|装\s*订\s*线|不要答题|不得答题")
_SECTION_RE = re.compile(
    r"^\s*(?:[一二三四五六七八九十]{1,3}\s*[、.．]|第[一二三四五六七八九十]+[部分大题]|[ⅠⅡⅢⅣ]+\s*[、.．])\s*\S"
)
_QUESTION_START_RE = re.compile(r"^\s*[*※▲]?\s*(\d{1,3})\s*[.．、]\s*(.*)$", re.S)
_OPTION_TOKEN_RE = re.compile(r"(?:^|(?<=[\s\t　;；]))([A-HＡ-Ｈ])\s*[.．、:：)）]\s*")
_SINGLE_CELL_OPT_RE = re.compile(r"^\s*([A-HＡ-Ｈ])\s*[.．、:：)）]\s*(.*)$", re.S)


def _local(el: Any) -> str:
    tag = str(getattr(el, "tag", "") or "")
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _w_attr(el: Any, name: str, default: str = "") -> str:
    for key in (f"{{{WORD_NS}}}{name}", f"{{{REL_NS}}}{name}", name):
        val = el.get(key)
        if val is not None:
            return str(val)
    return default


def _cn_numeral(n: int) -> str:
    if 1 <= n <= 10:
        return _CN_DIGITS[n]
    if 11 <= n <= 19:
        return "十" + _CN_DIGITS[n - 10]
    if 20 <= n <= 99:
        tens, ones = divmod(n, 10)
        return _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")
    return str(n)


def _format_num(n: int, fmt: str) -> str:
    if fmt in {"chineseCounting", "chineseCountingThousand", "ideographTraditional", "taiwaneseCounting"}:
        return _cn_numeral(n)
    if fmt == "upperLetter":
        return chr(ord("A") + (max(1, n) - 1) % 26)
    if fmt == "lowerLetter":
        return chr(ord("a") + (max(1, n) - 1) % 26)
    if fmt == "upperRoman":
        romans = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X")
        return romans[n - 1] if 1 <= n <= len(romans) else str(n)
    return str(n)


class _NumberingResolver:
    """Resolve Word automatic paragraph numbering (word/numbering.xml + word/styles.xml)."""

    def __init__(self, numbering_root: Any | None, styles_root: Any | None) -> None:
        self.abstract_lvls: dict[tuple[str, int], dict[str, Any]] = {}
        self.num_to_abstract: dict[str, str] = {}
        self.overrides: dict[tuple[str, int], int] = {}
        self.style_numpr: dict[str, tuple[str, int]] = {}
        self.counters: dict[tuple[str, int], int] = {}

        if styles_root is not None:
            for st in styles_root.findall(f".//{{{WORD_NS}}}style"):
                sid = _w_attr(st, "styleId")
                num_pr = st.find(f".//{{{WORD_NS}}}pPr/{{{WORD_NS}}}numPr")
                if sid and num_pr is not None:
                    nid_el = num_pr.find(f"{{{WORD_NS}}}numId")
                    ilvl_el = num_pr.find(f"{{{WORD_NS}}}ilvl")
                    if nid_el is not None:
                        nid = _w_attr(nid_el, "val")
                        ilvl = int(_w_attr(ilvl_el, "val", "0") or "0") if ilvl_el is not None else 0
                        if nid and nid != "0":
                            self.style_numpr[sid] = (nid, ilvl)

        if numbering_root is not None:
            for abs_el in numbering_root.findall(f"{{{WORD_NS}}}abstractNum"):
                aid = _w_attr(abs_el, "abstractNumId")
                for lvl in abs_el.findall(f"{{{WORD_NS}}}lvl"):
                    ilvl = int(_w_attr(lvl, "ilvl", "0") or "0")
                    start_el = lvl.find(f"{{{WORD_NS}}}start")
                    fmt_el = lvl.find(f"{{{WORD_NS}}}numFmt")
                    txt_el = lvl.find(f"{{{WORD_NS}}}lvlText")
                    suff_el = lvl.find(f"{{{WORD_NS}}}suff")
                    self.abstract_lvls[(aid, ilvl)] = {
                        "start": int(_w_attr(start_el, "val", "1") or "1") if start_el is not None else 1,
                        "fmt": _w_attr(fmt_el, "val", "decimal") if fmt_el is not None else "decimal",
                        "text": _w_attr(txt_el, "val", f"%{ilvl + 1}.") if txt_el is not None else f"%{ilvl + 1}.",
                        "suff": _w_attr(suff_el, "val", "tab") if suff_el is not None else "tab",
                    }
            for num_el in numbering_root.findall(f"{{{WORD_NS}}}num"):
                nid = _w_attr(num_el, "numId")
                abs_ref = num_el.find(f"{{{WORD_NS}}}abstractNumId")
                if nid and abs_ref is not None:
                    self.num_to_abstract[nid] = _w_attr(abs_ref, "val")
                for ov in num_el.findall(f"{{{WORD_NS}}}lvlOverride"):
                    ilvl = int(_w_attr(ov, "ilvl", "0") or "0")
                    st_ov = ov.find(f"{{{WORD_NS}}}startOverride")
                    if st_ov is not None:
                        self.overrides[(nid, ilvl)] = int(_w_attr(st_ov, "val", "1") or "1")

    def prefix_for_paragraph(self, p_el: Any) -> str:
        ppr = p_el.find(f"{{{WORD_NS}}}pPr")
        if ppr is None:
            return ""
        nid = ""
        ilvl = 0
        num_pr = ppr.find(f"{{{WORD_NS}}}numPr")
        if num_pr is not None:
            nid_el = num_pr.find(f"{{{WORD_NS}}}numId")
            ilvl_el = num_pr.find(f"{{{WORD_NS}}}ilvl")
            nid = _w_attr(nid_el, "val") if nid_el is not None else ""
            ilvl = int(_w_attr(ilvl_el, "val", "0") or "0") if ilvl_el is not None else 0
            if nid == "0":
                return ""
        if not nid:
            pstyle = ppr.find(f"{{{WORD_NS}}}pStyle")
            sid = _w_attr(pstyle, "val") if pstyle is not None else ""
            if sid in self.style_numpr:
                nid, ilvl = self.style_numpr[sid]
        if not nid or nid not in self.num_to_abstract:
            return ""
        aid = self.num_to_abstract[nid]
        spec = self.abstract_lvls.get((aid, ilvl))
        if not spec or spec["fmt"] == "bullet":
            return ""
        key = (nid, ilvl)
        if key not in self.counters:
            self.counters[key] = self.overrides.get(key, spec["start"])
        else:
            self.counters[key] += 1
        for deeper in range(ilvl + 1, 9):
            self.counters.pop((nid, deeper), None)

        rendered = str(spec["text"])
        for lv in range(ilvl + 1):
            lv_spec = self.abstract_lvls.get((aid, lv), spec)
            val = self.counters.get((nid, lv), lv_spec["start"])
            rendered = rendered.replace(f"%{lv + 1}", _format_num(val, lv_spec["fmt"]))
        sep = " " if spec["suff"] in {"tab", "space"} else ""
        return f"{rendered}{sep}"


def _decode_media_to_png(raw_bytes: bytes) -> dict[str, Any] | None:
    """Convert embedded Word raster/metafile bytes into a verified PNG asset dict."""
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.load()
            if img.width < 2 or img.height < 2:
                return None
            if img.mode in {"RGBA", "LA"} or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[3])
                rgb = bg
            else:
                rgb = img.convert("RGB")
            buf = io.BytesIO()
            rgb.save(buf, format="PNG", optimize=True)
            png_bytes = buf.getvalue()
            sha = hashlib.sha256(png_bytes).hexdigest()
            return {"bytes": png_bytes, "width": rgb.width, "height": rgb.height, "sha256": sha}
    except Exception:
        return None


class _DocxContentReader:
    def __init__(self, archive: zipfile.ZipFile, rels: dict[str, str], num_resolver: _NumberingResolver) -> None:
        self.archive = archive
        self.rels = rels
        self.num = num_resolver
        self._media_cache: dict[str, dict[str, Any] | None] = {}

    def _read_rel_bytes(self, r_id: str) -> tuple[str, bytes] | None:
        target = self.rels.get(r_id)
        if not target:
            return None
        clean = target.lstrip("/")
        if not clean.startswith("word/"):
            clean = posixpath.normpath(posixpath.join("word", clean))
        try:
            return clean, self.archive.read(clean)
        except KeyError:
            return None

    def _media_asset(self, r_id: str) -> tuple[dict[str, Any] | None, bool]:
        """Return (decoded_png_dict, is_unreadable_vector_metafile)."""
        if r_id in self._media_cache:
            cached = self._media_cache[r_id]
            return cached, cached is None
        fetched = self._read_rel_bytes(r_id)
        if fetched is None:
            return None, False
        path_str, raw = fetched
        decoded = _decode_media_to_png(raw)
        self._media_cache[r_id] = decoded
        is_meta = path_str.lower().endswith((".wmf", ".emf", ".emz", ".wmz"))
        return decoded, (decoded is None and is_meta)

    def _inspect_visual_container(self, container: Any, state: dict[str, Any]) -> str:
        """Inspect <w:object>, <w:drawing>, <w:pict>, or <mc:AlternateContent>."""
        tag = _local(container)
        if tag == "AlternateContent":
            choice = container.find(f"{{{MC_NS}}}Choice")
            fallback = container.find(f"{{{MC_NS}}}Fallback")
            if choice is not None:
                # If Choice has OMML, raster blip, or OLE object, prefer Choice alone to avoid duplicating Fallback
                has_primary = bool(
                    choice.findall(f".//{{{OMML_NS}}}oMath")
                    or choice.findall(f".//{{{DRAWING_A_NS}}}blip")
                    or choice.findall(f".//{{{OFFICE_O_NS}}}OLEObject")
                )
                if has_primary or fallback is None:
                    return self._inspect_visual_container(choice, state)
                # Vector shape in Choice: check if Fallback has a rasterized preview image
                fb_blips = fallback.findall(f".//{{{VML_NS}}}imagedata") + fallback.findall(
                    f".//{{{DRAWING_A_NS}}}blip"
                )
                if fb_blips:
                    return self._inspect_visual_container(fallback, state)
                return self._inspect_visual_container(choice, state)
            if fallback is not None:
                return self._inspect_visual_container(fallback, state)
            return ""

        # Check for embedded OMML inside textboxes/shapes
        omaths = container.findall(f".//{{{OMML_NS}}}oMath")
        if omaths and not container.findall(f".//{{{OFFICE_O_NS}}}OLEObject"):
            parts = []
            for om in omaths:
                tex, warns = omml_to_latex(om)
                if tex:
                    parts.append(f"${tex}$")
                if warns:
                    state["omml_warnings"].extend(warns)
            if parts:
                return " ".join(parts)

        # 1. Check OLE Equation (MathType / Microsoft Equation 3.0)
        ole_nodes = container.findall(f".//{{{OFFICE_O_NS}}}OLEObject")
        if ole_nodes:
            for ole in ole_nodes:
                ole_rid = _w_attr(ole, "id")
                fetched = self._read_rel_bytes(ole_rid) if ole_rid else None
                if fetched is not None:
                    latex = ole_equation_to_latex(fetched[1])
                    if latex:
                        return f"${latex}$"
                # Fallback: preserve preview image + mark yellow flag
                state["ole_fallback"] = True
                for img_el in container.findall(f".//{{{VML_NS}}}imagedata"):
                    img_rid = _w_attr(img_el, "id")
                    if img_rid:
                        asset, unreadable_meta = self._media_asset(img_rid)
                        if asset is not None:
                            state["images"].append(asset)
                        elif unreadable_meta:
                            state["unreadable_meta"] = True
            return ""

        # 2. Check embedded raster images (DrawingML a:blip or VML v:imagedata)
        found_raster = False
        for blip in container.findall(f".//{{{DRAWING_A_NS}}}blip"):
            embed_id = blip.get(f"{{{REL_NS}}}embed") or ""
            if embed_id:
                asset, unreadable_meta = self._media_asset(embed_id)
                if asset is not None:
                    state["images"].append(asset)
                    found_raster = True
                elif unreadable_meta:
                    state["unreadable_meta"] = True
                    found_raster = True
        if not found_raster:
            for img_el in container.findall(f".//{{{VML_NS}}}imagedata"):
                img_rid = _w_attr(img_el, "id")
                if img_rid:
                    asset, unreadable_meta = self._media_asset(img_rid)
                    if asset is not None:
                        state["images"].append(asset)
                        found_raster = True
                    elif unreadable_meta:
                        state["unreadable_meta"] = True
                        found_raster = True
        if found_raster:
            return ""

        # 3. Check Word vector shapes / InkML (lines, arcs, grouped geometry without raster blip)
        inner_text = "".join(t.text or "" for t in container.findall(f".//{{{WORD_NS}}}t")).strip()
        if inner_text and _SEAL_LINE_RE.search(inner_text):
            return ""
        vector_tags = {
            "wsp",
            "wgp",
            "line",
            "oval",
            "arc",
            "polyline",
            "curve",
            "rect",
            "shape",
            "group",
            "contentPart",
        }
        if any(_local(sub) in vector_tags for sub in container.iter()):
            state["has_vector"] = True
            if inner_text and len(inner_text) <= 24:
                return ""
        return inner_text

    def _read_run(self, r_el: Any, state: dict[str, Any]) -> tuple[str, str]:
        """Return (text, vert_align) where vert_align is '', 'superscript', or 'subscript'."""
        rpr = r_el.find(f"{{{WORD_NS}}}rPr")
        vert = ""
        has_underline = False
        if rpr is not None:
            va = rpr.find(f"{{{WORD_NS}}}vertAlign")
            if va is not None:
                vert = _w_attr(va, "val")
            u_el = rpr.find(f"{{{WORD_NS}}}u")
            if u_el is not None and _w_attr(u_el, "val", "single") not in {"none", "0", "false"}:
                has_underline = True

        chunks: list[str] = []
        for child in r_el:
            ctag = _local(child)
            if ctag == "t":
                raw = child.text or ""
                if has_underline and raw and not raw.strip() and len(raw) >= 2:
                    chunks.append("______")
                else:
                    chunks.append(raw)
            elif ctag == "tab":
                chunks.append("______" if has_underline else "\t")
            elif ctag in {"br", "cr"}:
                if _w_attr(child, "type") == "page":
                    state["page_break"] = True
                else:
                    chunks.append("\n")
            elif ctag == "sym":
                code = _w_attr(child, "char").upper()
                chunks.append(_SYM_CHAR_MAP.get(code, ""))
            elif ctag in {"drawing", "pict", "object", "AlternateContent", "contentPart"}:
                piece = self._inspect_visual_container(child, state)
                if piece:
                    chunks.append(piece)
        return "".join(chunks), vert

    def _walk_inline(self, parent: Any, out_tokens: list[tuple[str, str]], state: dict[str, Any]) -> None:
        for child in parent:
            tag = _local(child)
            if tag in {
                "pPr",
                "del",
                "commentRangeStart",
                "commentRangeEnd",
                "bookmarkStart",
                "bookmarkEnd",
                "proofErr",
            }:
                continue
            if tag == "r":
                txt, vert = self._read_run(child, state)
                if txt:
                    out_tokens.append((txt, vert))
            elif tag == "oMathPara":
                omaths = [sub for sub in child if _local(sub) == "oMath"] or [child]
                rendered = []
                for om in omaths:
                    tex, warns = omml_to_latex(om)
                    if tex:
                        rendered.append(tex)
                    if warns:
                        state["omml_warnings"].extend(warns)
                if rendered:
                    out_tokens.append((f"$${' '.join(rendered)}$$", "math"))
            elif tag == "oMath":
                tex, warns = omml_to_latex(child)
                if tex:
                    out_tokens.append((f"${tex}$", "math"))
                if warns:
                    state["omml_warnings"].extend(warns)
            elif tag in {"AlternateContent", "drawing", "pict", "object", "contentPart"}:
                piece = self._inspect_visual_container(child, state)
                if piece:
                    out_tokens.append((piece, ""))
            elif tag in {"hyperlink", "smartTag", "ins", "sdt", "sdtContent", "customXml", "fldSimple"}:
                self._walk_inline(child, out_tokens, state)

    def read_paragraph(self, p_el: Any, *, allow_numbering: bool = True) -> dict[str, Any]:
        state: dict[str, Any] = {
            "images": [],
            "has_vector": False,
            "ole_fallback": False,
            "unreadable_meta": False,
            "omml_warnings": [],
            "page_break": False,
        }
        prefix = self.num.prefix_for_paragraph(p_el) if allow_numbering else ""
        tokens: list[tuple[str, str]] = []
        self._walk_inline(p_el, tokens, state)

        # Merge adjacent superscript/subscript runs into clean LaTeX
        assembled: list[str] = []
        i = 0
        while i < len(tokens):
            txt, vert = tokens[i]
            if vert in {"superscript", "subscript"} and txt.strip():
                op = "^" if vert == "superscript" else "_"
                script_val = txt.strip()
                if assembled and assembled[-1] and not assembled[-1].endswith("$"):
                    prev = assembled[-1]
                    m_base = re.search(r"([A-Za-z0-9)\]}]+)$", prev)
                    if m_base:
                        base = m_base.group(1)
                        assembled[-1] = prev[: m_base.start()] + f"${base}{op}{{{script_val}}}$"
                        i += 1
                        continue
                assembled.append(f"${op}{{{script_val}}}$")
            else:
                assembled.append(txt)
            i += 1

        body_text = "".join(assembled)
        # Collapse inline $$...$$ into $...$ if surrounded by text on the same paragraph
        if "$$" in body_text and body_text.strip() != body_text[body_text.find("$$") : body_text.rfind("$$") + 2]:
            body_text = re.sub(r"\$\$(.+?)\$\$", r"$\1$", body_text)

        full_text = f"{prefix}{body_text}" if (prefix and (body_text.strip() or state["images"])) else body_text
        full_text = re.sub(r"_{6,}", "______", full_text)
        return {
            "kind": "paragraph",
            "text": full_text.strip("\r"),
            "images": state["images"],
            "has_vector": state["has_vector"],
            "ole_fallback": state["ole_fallback"],
            "unreadable_meta": state["unreadable_meta"],
            "is_table": False,
        }

    def read_table(self, tbl_el: Any) -> dict[str, Any]:
        raw_grid: list[list[dict[str, Any]]] = []
        all_images: list[dict[str, Any]] = []
        has_vector = False
        ole_fallback = False
        unreadable_meta = False

        for tr in [c for c in tbl_el if _local(c) == "tr"]:
            row_cells: list[dict[str, Any]] = []
            for tc in [c for c in tr if _local(c) == "tc"]:
                tc_pr = tc.find(f"{{{WORD_NS}}}tcPr")
                colspan = 1
                vmerge: str | None = None
                if tc_pr is not None:
                    gs = tc_pr.find(f"{{{WORD_NS}}}gridSpan")
                    if gs is not None:
                        colspan = max(1, int(_w_attr(gs, "val", "1") or "1"))
                    vm = tc_pr.find(f"{{{WORD_NS}}}vMerge")
                    if vm is not None:
                        vmerge = _w_attr(vm, "val", "continue") or "continue"

                p_texts: list[str] = []
                cell_imgs: list[dict[str, Any]] = []
                for p in [c for c in tc if _local(c) == "p"]:
                    p_info = self.read_paragraph(p, allow_numbering=False)
                    if p_info["text"].strip():
                        p_texts.append(p_info["text"].strip())
                    cell_imgs.extend(p_info["images"])
                    has_vector = has_vector or p_info["has_vector"]
                    ole_fallback = ole_fallback or p_info["ole_fallback"]
                    unreadable_meta = unreadable_meta or p_info["unreadable_meta"]
                all_images.extend(cell_imgs)
                row_cells.append(
                    {
                        "text": " ".join(p_texts).strip(),
                        "images": cell_imgs,
                        "colspan": colspan,
                        "vmerge": vmerge,
                        "rowspan": 1,
                    }
                )
            if row_cells:
                raw_grid.append(row_cells)

        # Resolve vertical merges (vMerge='restart' followed by 'continue')
        has_merge = False
        for r_idx, row in enumerate(raw_grid):
            col_cursor = 0
            for cell in row:
                cell["_col"] = col_cursor
                if cell["colspan"] > 1:
                    has_merge = True
                if cell["vmerge"] == "continue":
                    has_merge = True
                    for prev_r in range(r_idx - 1, -1, -1):
                        anchor = next(
                            (c for c in raw_grid[prev_r] if c.get("_col") == col_cursor and c["vmerge"] != "continue"),
                            None,
                        )
                        if anchor is not None:
                            anchor["rowspan"] += 1
                            if cell["text"]:
                                anchor["text"] = f"{anchor['text']} {cell['text']}".strip()
                            anchor["images"].extend(cell["images"])
                            break
                col_cursor += cell["colspan"]

        # Check if this table is an option-layout table (A., B., C., D. ...)
        flat_cells = [c for row in raw_grid for c in row if c["vmerge"] != "continue" and (c["text"] or c["images"])]
        option_cells: dict[str, dict[str, Any]] = {}
        if 2 <= len(flat_cells) <= 8:
            valid_opt_table = True
            for cell in flat_cells:
                # A single cell might even hold "A. xxx   B. yyy" or just "A. xxx"
                m = _SINGLE_CELL_OPT_RE.match(cell["text"])
                if not m:
                    valid_opt_table = False
                    break
                key = (
                    chr(ord(m.group(1).upper()) - 0xFEE0) if "Ａ" <= m.group(1).upper() <= "Ｈ" else m.group(1).upper()
                )
                if key in option_cells:
                    valid_opt_table = False
                    break
                option_cells[key] = {"text": m.group(2).strip(), "images": list(cell["images"])}
            if not valid_opt_table or "A" not in option_cells or "B" not in option_cells:
                option_cells = {}

        plain_rows = [[c["text"] for c in row if c["vmerge"] != "continue"] for row in raw_grid]
        if option_cells:
            rendered = "\n".join(f"{k}. {v['text']}".rstrip() for k, v in option_cells.items())
        elif has_merge:
            tr_html: list[str] = []
            for row in raw_grid:
                tds: list[str] = []
                for c in row:
                    if c["vmerge"] == "continue":
                        continue
                    attrs = ""
                    if c["rowspan"] > 1:
                        attrs += f' rowspan="{c["rowspan"]}"'
                    if c["colspan"] > 1:
                        attrs += f' colspan="{c["colspan"]}"'
                    tds.append(f"<td{attrs}>{html_lib.escape(c['text'], quote=False)}</td>")
                if tds:
                    tr_html.append(f"<tr>{''.join(tds)}</tr>")
            rendered = f"<table>{''.join(tr_html)}</table>" if tr_html else ""
        else:
            width = max((len(r) for r in plain_rows), default=0)
            pipe_lines: list[str] = []
            for r_idx, r in enumerate(plain_rows):
                padded = [self._escape_pipe(cell) for cell in r] + [""] * (width - len(r))
                pipe_lines.append("| " + " | ".join(padded) + " |")
                if r_idx == 0 and len(plain_rows) >= 2:
                    pipe_lines.append("|" + "|".join(["---"] * width) + "|")
            rendered = "\n".join(pipe_lines) if width > 0 else ""

        return {
            "kind": "table",
            "text": rendered,
            "images": all_images,
            "has_vector": has_vector,
            "ole_fallback": ole_fallback,
            "unreadable_meta": unreadable_meta,
            "is_table": True,
            "table_rows": plain_rows,
            "option_cells": option_cells or None,
        }

    @staticmethod
    def _escape_pipe(text: str) -> str:
        parts = re.split(r"(\$[^$]*\$)", text)
        return "".join(p if p.startswith("$") else p.replace("|", r"\|") for p in parts)


def _norm_letter(raw: str) -> str:
    ch = raw.strip().upper()
    if "Ａ" <= ch <= "Ｈ":
        return chr(ord(ch) - 0xFEE0)
    return ch


def _extract_stem_and_options(q_blocks: list[dict[str, Any]]) -> tuple[str, dict[str, str], list[dict[str, Any]]]:
    """Split question blocks into (stem, options_dict, bound_figures_with_slot)."""
    stem_lines: list[str] = []
    options: dict[str, str] = {}
    figures: list[dict[str, Any]] = []
    current_opt: str | None = None

    for block in q_blocks:
        opt_cells = block.get("option_cells")
        if isinstance(opt_cells, dict) and opt_cells:
            for letter, spec in opt_cells.items():
                options[letter] = spec["text"]
                for img in spec["images"]:
                    figures.append({"slot": letter, **img})
                current_opt = letter
            continue

        text = str(block.get("text") or "")
        imgs = list(block.get("images") or [])
        if block.get("is_table"):
            if current_opt and current_opt in options:
                options[current_opt] = f"{options[current_opt]}\n{text}".strip()
                for img in imgs:
                    figures.append({"slot": current_opt, **img})
            else:
                if text.strip():
                    stem_lines.append(text.strip())
                for img in imgs:
                    figures.append({"slot": "stem", **img})
            continue

        # Check for option markers A. / B. / C. / D. in non-math segments of this paragraph
        hits = []
        for m in _OPTION_TOKEN_RE.finditer(text):
            # Ignore matches inside $...$ math spans
            prefix_dollars = len(re.findall(r"(?<!\\)\$", text[: m.start()]))
            if prefix_dollars % 2 == 1:
                continue
            hits.append((m.start(), m.end(), _norm_letter(m.group(1))))

        # Validate monotonic option progression (e.g. starts at A if no options yet, or continues next letter)
        valid_hits: list[tuple[int, int, str]] = []
        expected_ord = ord(current_opt) + 1 if current_opt else ord("A")
        for st, ed, letter in hits:
            if ord(letter) == expected_ord or (not options and not valid_hits and letter == "A"):
                valid_hits.append((st, ed, letter))
                expected_ord = ord(letter) + 1

        if valid_hits:
            first_st = valid_hits[0][0]
            before_opts = text[:first_st].strip()
            if before_opts:
                if current_opt and current_opt in options:
                    options[current_opt] = f"{options[current_opt]}\n{before_opts}".strip()
                else:
                    stem_lines.append(before_opts)
            for idx, (_st, ed, letter) in enumerate(valid_hits):
                next_st = valid_hits[idx + 1][0] if idx + 1 < len(valid_hits) else len(text)
                val = text[ed:next_st].strip()
                options[letter] = val
                current_opt = letter
            if imgs:
                if len(valid_hits) > 1 and len(imgs) == len(valid_hits) and all(not options[h[2]] for h in valid_hits):
                    for (_st, _ed, letter), img in zip(valid_hits, imgs):
                        figures.append({"slot": letter, **img})
                else:
                    slot = current_opt if (len(valid_hits) == 1 and current_opt) else "stem"
                    for img in imgs:
                        figures.append({"slot": slot, **img})
        else:
            stripped = text.strip()
            if stripped:
                if current_opt and current_opt in options:
                    options[current_opt] = f"{options[current_opt]}\n{stripped}".strip()
                else:
                    stem_lines.append(stripped)
            for img in imgs:
                figures.append({"slot": current_opt or "stem", **img})

    if "A" not in options or "B" not in options:
        # A solitary "A." without "B." is not a multiple-choice option list
        if options:
            restored = [f"{k}. {v}".strip() for k, v in options.items()]
            stem_lines.extend(restored)
            options = {}
            for fig in figures:
                fig["slot"] = "stem"

    return "\n".join(stem_lines).strip(), options, figures


def _infer_word_question_type(
    section: str,
    labelled_type: str | None,
    stem: str,
    options: dict[str, str],
    figures: list[dict[str, Any]],
    answer: str,
) -> str:
    sec_choice = qtypes.section_kind(section)
    sec_word = qtypes.from_words(section)
    has_opts = bool(options) or any(f.get("slot") in set("ABCDEFGH") for f in figures)
    clean_ans = answer.strip().upper()

    if labelled_type and qtypes.decided(labelled_type):
        return labelled_type
    if has_opts:
        if sec_choice in qtypes.CHOICE_TYPES:
            return sec_choice
        if re.fullmatch(r"[A-H]{2,8}", clean_ans):
            return "multiple_choice"
        return qtypes.infer("unknown", stem, options or {"A": "x", "B": "y"})
    if sec_word in {"fill_blank", "true_false", "free_response"}:
        return sec_word
    if clean_ans in {"√", "×", "✓", "✗", "对", "错"}:
        return "true_false"
    inferred = qtypes.infer("unknown", stem, {})
    if qtypes.decided(inferred):
        return inferred
    if re.search(r"_{3,}|\\underline|（\s*）|\(\s*\)", stem) and not qtypes.subquestion_count(stem):
        return "fill_blank"
    return "unknown"


def _iter_body_blocks(body: Any, reader: _DocxContentReader) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []

    def visit(container: Any) -> None:
        for child in container:
            tag = _local(child)
            if tag == "p":
                blocks.append(reader.read_paragraph(child))
            elif tag == "tbl":
                blocks.append(reader.read_table(child))
            elif tag in {"sdt", "sdtContent", "customXml"}:
                visit(child)

    visit(body)
    return blocks


def extract_docx(source: Path) -> dict[str, Any]:
    """Extract structured questions, answers, analyses, images, and warnings from a .docx file."""
    from lxml import etree

    if not zipfile.is_zipfile(source):
        raise ValueError("不是有效的 DOCX 压缩文档")

    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False)
    with zipfile.ZipFile(source, "r") as archive:
        if "word/document.xml" not in archive.namelist():
            raise ValueError("DOCX 中缺少 word/document.xml 正文")

        rels: dict[str, str] = {}
        if "word/_rels/document.xml.rels" in archive.namelist():
            rels_root = etree.fromstring(archive.read("word/_rels/document.xml.rels"), parser)
            for rel in rels_root.findall(f"{{{PKG_REL_NS}}}Relationship"):
                rid = rel.get("Id")
                target = rel.get("Target")
                if rid and target and rel.get("TargetMode") != "External":
                    rels[rid] = target

        num_root = (
            etree.fromstring(archive.read("word/numbering.xml"), parser)
            if "word/numbering.xml" in archive.namelist()
            else None
        )
        styles_root = (
            etree.fromstring(archive.read("word/styles.xml"), parser)
            if "word/styles.xml" in archive.namelist()
            else None
        )
        doc_root = etree.fromstring(archive.read("word/document.xml"), parser)
        body = doc_root.find(f"{{{WORD_NS}}}body")
        if body is None:
            raise ValueError("DOCX 正文为空")

        reader = _DocxContentReader(archive, rels, _NumberingResolver(num_root, styles_root))
        all_blocks = _iter_body_blocks(body, reader)

    # 1. Split document body into exam blocks and optional end-of-document answer chapter
    answer_split_idx: int | None = None
    for idx, block in enumerate(all_blocks):
        if not block.get("is_table") and printed_answers.is_answer_chapter_heading(block.get("text", "")):
            answer_split_idx = idx
            break

    exam_blocks = all_blocks[:answer_split_idx] if answer_split_idx is not None else all_blocks
    answer_chapter_blocks = all_blocks[answer_split_idx + 1 :] if answer_split_idx is not None else []

    # 2. Segment exam_blocks into sections & numbered questions (skipping front-matter instructions)
    raw_questions: list[dict[str, Any]] = []
    current_section = ""
    group_idx = 0
    in_notice = False
    current_q: dict[str, Any] | None = None

    def flush_q() -> None:
        nonlocal current_q
        if current_q is not None:
            raw_questions.append(current_q)
            current_q = None

    for block in exam_blocks:
        text = str(block.get("text") or "").strip()
        has_imgs = bool(block.get("images"))
        if not text and not has_imgs and not block.get("has_vector") and not block.get("ole_fallback"):
            continue

        if not block.get("is_table") and _NOTICE_HEADER_RE.match(text):
            in_notice = True
            continue
        if not block.get("is_table") and _SECTION_RE.match(text) and not _QUESTION_START_RE.match(text):
            in_notice = False
            flush_q()
            current_section = text[:120]
            continue

        q_match = None if block.get("is_table") else _QUESTION_START_RE.match(text)
        if q_match:
            num = int(q_match.group(1))
            rest = q_match.group(2).strip()
            if in_notice and not raw_questions and not current_section and _INSTRUCTION_RE.search(rest):
                continue
            in_notice = False
            if current_q is not None and num == 1 and current_q["number"] >= 1:
                # Per-section restarted numbering (e.g., 二、填空题 starts from 1 again)
                flush_q()
                group_idx += 1
            else:
                flush_q()
            first_block = {**block, "text": rest}
            current_q = {"group_idx": group_idx, "section": current_section, "number": num, "blocks": [first_block]}
            continue

        if current_q is not None:
            current_q["blocks"].append(block)

    flush_q()

    # 3. Parse each question: strip inline answers/analyses, parse options, normalize prose/math
    parsed_questions: list[dict[str, Any]] = []
    for item in raw_questions:
        sec_word = qtypes.from_words(item["section"])
        split = printed_answers.split_question_blocks(
            item["blocks"], allow_worked_start=(sec_word not in qtypes.CHOICE_TYPES)
        )
        q_blocks = split["question_blocks"]
        raw_stem, raw_opts, bound_figs = _extract_stem_and_options(q_blocks)
        fixed_stem = textnorm.fix_symbols(raw_stem)
        fixed_opts = {k: textnorm.fix_symbols(v) for k, v in raw_opts.items()}
        fixed_ans = textnorm.fix_symbols(split["answer"])
        fixed_ana = textnorm.fix_symbols(split["analysis"])

        tidied = prose.tidy_fields(
            {
                "stem": fixed_stem,
                "options": fixed_opts,
                "answer": fixed_ans,
                "analysis": fixed_ana,
                "question_type": "unknown",
            }
        )
        qtype = _infer_word_question_type(
            item["section"],
            tidied.get("question_type"),
            tidied["stem"],
            tidied.get("options") or {},
            bound_figs,
            tidied.get("answer") or "",
        )
        flags: list[str] = []
        if any(b.get("ole_fallback") for b in item["blocks"]):
            flags.append(FLAG_OLE_EQUATION_FALLBACK)
        if any(b.get("has_vector") for b in q_blocks):
            flags.append(FLAG_VECTOR_DRAWING)
        if any(b.get("unreadable_meta") for b in item["blocks"]):
            flags.append(FLAG_VECTOR_IMAGE_UNREADABLE)

        parsed_questions.append(
            {
                "group_idx": item["group_idx"],
                "section": item["section"],
                "number": item["number"],
                "question_type": qtype,
                "stem": tidied["stem"].strip(),
                "options": tidied.get("options") or {},
                "origin": tidied.get("origin") or "",
                "answer": (tidied.get("answer") or "").strip(),
                "analysis": (tidied.get("analysis") or "").strip(),
                "figures": bound_figs,
                "solution_images": list(split["solution_images"]),
                "flags": flags,
            }
        )

    # 4. Align end-of-document answer chapter entries onto parsed_questions
    warnings: list[str] = []
    if answer_chapter_blocks:
        chapter_entries = printed_answers.parse_answer_chapter(answer_chapter_blocks)
        _align_chapter_answers(parsed_questions, chapter_entries, warnings)

    return {
        "questions": parsed_questions,
        "all_blocks": all_blocks,
        "exam_blocks": exam_blocks,
        "has_answer_chapter": bool(answer_chapter_blocks),
        "warnings": warnings,
    }


def _align_chapter_answers(questions: list[dict[str, Any]], entries: list[dict[str, Any]], warnings: list[str]) -> None:
    if not entries:
        warnings.append("检测到卷末参考答案标题，但未解析出按题号排列的答案条目，请人工核对。")
        for q in questions:
            if not q["answer"] and not q["analysis"]:
                q["flags"].append(f"{FLAG_ANSWER_MISMATCH_PREFIX}未自动匹配到本题答案，请对照原卷核对")
        return

    by_group_num: dict[tuple[int, int], list[dict[str, Any]]] = {}
    by_num: dict[int, list[dict[str, Any]]] = {}
    for q in questions:
        by_group_num.setdefault((q["group_idx"], q["number"]), []).append(q)
        by_num.setdefault(q["number"], []).append(q)

    matched_q_ids: set[int] = set()
    unmatched_nums: list[int] = []

    for entry in entries:
        num = entry["number"]
        sec_idx = entry["section_idx"]
        target: dict[str, Any] | None = None
        candidates_gn = by_group_num.get((sec_idx, num), [])
        candidates_n = by_num.get(num, [])

        if len(candidates_gn) == 1:
            target = candidates_gn[0]
        elif len(candidates_n) == 1:
            target = candidates_n[0]
        elif len(candidates_n) > 1:
            for cand in candidates_n:
                cand["flags"].append(
                    f"{FLAG_ANSWER_MISMATCH_PREFIX}题号 {num} 在卷中重复出现，答案未能唯一对应，请对照原卷核对"
                )
            unmatched_nums.append(num)
            continue
        else:
            unmatched_nums.append(num)
            continue

        matched_q_ids.add(id(target))
        ans = prose.tidy_value(textnorm.fix_symbols(entry["answer"])).strip()
        ana = prose.tidy_value(textnorm.fix_symbols(entry["analysis"])).strip()
        if target["answer"] and ans and target["answer"] != ans:
            target["flags"].append(
                f"{FLAG_ANSWER_MISMATCH_PREFIX}随题答案（{target['answer'][:24]}）与卷末答案（{ans[:24]}）不一致，请核对"
            )
        elif ans and not target["answer"]:
            target["answer"] = ans
            if target["question_type"] == "single_choice" and re.fullmatch(r"[A-H]{2,8}", ans.upper()):
                target["question_type"] = "multiple_choice"

        if ana:
            if target["analysis"] and ana not in target["analysis"]:
                target["analysis"] = f"{target['analysis']}\n{ana}".strip()
            elif not target["analysis"]:
                target["analysis"] = ana
        if entry["images"]:
            target["solution_images"].extend(entry["images"])

    if unmatched_nums:
        nums_str = "、".join(str(n) for n in sorted(set(unmatched_nums))[:10])
        warnings.append(f"卷末参考答案中有题号（第 {nums_str} 题）未能对应到正文题目，请对照原卷核对。")

    for q in questions:
        if id(q) not in matched_q_ids and not q["answer"] and not q["analysis"]:
            q["flags"].append(
                f"{FLAG_ANSWER_MISMATCH_PREFIX}本卷附有参考答案章节，但未匹配到第 {q['number']} 题答案，请核对"
            )

"""Convert OOXML Office Math Markup Language (OMML) elements into KaTeX LaTeX."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

OMML_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_SYMBOL_MAP: dict[str, str] = {
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "ε": r"\varepsilon",
    "ϵ": r"\epsilon",
    "ζ": r"\zeta",
    "η": r"\eta",
    "θ": r"\theta",
    "ϑ": r"\vartheta",
    "ι": r"\iota",
    "κ": r"\kappa",
    "λ": r"\lambda",
    "μ": r"\mu",
    "ν": r"\nu",
    "ξ": r"\xi",
    "π": r"\pi",
    "ϖ": r"\varpi",
    "ρ": r"\rho",
    "ϱ": r"\varrho",
    "σ": r"\sigma",
    "ς": r"\varsigma",
    "τ": r"\tau",
    "υ": r"\upsilon",
    "φ": r"\varphi",
    "ϕ": r"\phi",
    "χ": r"\chi",
    "ψ": r"\psi",
    "ω": r"\omega",
    "Γ": r"\Gamma",
    "Δ": r"\Delta",
    "Θ": r"\Theta",
    "Λ": r"\Lambda",
    "Ξ": r"\Xi",
    "Π": r"\Pi",
    "Σ": r"\Sigma",
    "Υ": r"\Upsilon",
    "Φ": r"\Phi",
    "Ψ": r"\Psi",
    "Ω": r"\Omega",
    "±": r"\pm",
    "∓": r"\mp",
    "×": r"\times",
    "÷": r"\div",
    "·": r"\cdot",
    "∙": r"\cdot",
    "≠": r"\neq",
    "≤": r"\leqslant",
    "⩽": r"\leqslant",
    "≥": r"\geqslant",
    "⩾": r"\geqslant",
    "≪": r"\ll",
    "≫": r"\gg",
    "≈": r"\approx",
    "≡": r"\equiv",
    "≅": r"\cong",
    "∼": r"\sim",
    "∽": r"\backsim",
    "∝": r"\propto",
    "∞": r"\infty",
    "∈": r"\in",
    "∉": r"\notin",
    "∋": r"\ni",
    "⊂": r"\subset",
    "⊃": r"\supset",
    "⊆": r"\subseteq",
    "⊇": r"\supseteq",
    "⊊": r"\subsetneqq",
    "⊋": r"\supsetneqq",
    "∪": r"\cup",
    "∩": r"\cap",
    "∅": r"\varnothing",
    "⌀": r"\varnothing",
    "∀": r"\forall",
    "∃": r"\exists",
    "¬": r"\neg",
    "∧": r"\wedge",
    "∨": r"\vee",
    "⊕": r"\oplus",
    "⊗": r"\otimes",
    "⊥": r"\perp",
    "∥": r"\parallel",
    "∠": r"\angle",
    "△": r"\triangle",
    "▲": r"\blacktriangle",
    "▱": r"\square",
    "□": r"\square",
    "⊙": r"\odot",
    "°": r"^{\circ}",
    "℃": r"^{\circ}\mathrm{C}",
    "′": "'",
    "″": "''",
    "‴": "'''",
    "∂": r"\partial",
    "∇": r"\nabla",
    "→": r"\to",
    "←": r"\leftarrow",
    "↔": r"\leftrightarrow",
    "⇒": r"\Rightarrow",
    "⇐": r"\Leftarrow",
    "⇔": r"\Leftrightarrow",
    "↑": r"\uparrow",
    "↓": r"\downarrow",
    "⋯": r"\cdots",
    "…": r"\dots",
    "⋮": r"\vdots",
    "⋱": r"\ddots",
    "ℝ": r"\mathbb{R}",
    "ℤ": r"\mathbb{Z}",
    "ℕ": r"\mathbb{N}",
    "ℚ": r"\mathbb{Q}",
    "ℂ": r"\mathbb{C}",
    "ℓ": r"\ell",
    "ℏ": r"\hbar",
    "−": "-",
    "﹣": "-",
    "＋": "+",
    "＝": "=",
    "＜": "<",
    "＞": ">",
    "（": "(",
    "）": ")",
    "［": "[",
    "］": "]",
    "｛": r"\{",
    "｝": r"\}",
    "｜": "|",
    "，": ",",
    "；": ";",
    "：": ":",
}

_FUNCTIONS = (
    "arcsin",
    "arccos",
    "arctan",
    "sinh",
    "cosh",
    "tanh",
    "coth",
    "sin",
    "cos",
    "tan",
    "cot",
    "sec",
    "csc",
    "log",
    "ln",
    "lg",
    "lim",
    "max",
    "min",
    "sup",
    "inf",
    "det",
    "gcd",
    "lcm",
)
_FUNC_RE = re.compile(r"(?<!\\)\b(" + "|".join(_FUNCTIONS) + r")\b")

_NARY_MAP = {
    "∑": r"\sum",
    "∏": r"\prod",
    "∐": r"\coprod",
    "∫": r"\int",
    "∬": r"\iint",
    "∭": r"\iiint",
    "∮": r"\oint",
    "⋃": r"\bigcup",
    "⋂": r"\bigcap",
    "⋁": r"\bigvee",
    "⋀": r"\bigwedge",
}

_DELIM_LEFT = {
    "(": "(",
    "[": "[",
    "{": r"\{",
    "|": "|",
    "‖": r"\|",
    "∥": r"\|",
    "⌊": r"\lfloor",
    "⌈": r"\lceil",
    "⟨": r"\langle",
    "〈": r"\langle",
    "": ".",
}
_DELIM_RIGHT = {
    ")": ")",
    "]": "]",
    "}": r"\}",
    "|": "|",
    "‖": r"\|",
    "∥": r"\|",
    "⌋": r"\rfloor",
    "⌉": r"\rceil",
    "⟩": r"\rangle",
    "〉": r"\rangle",
    "": ".",
}


def _local(element: Any) -> str:
    tag = str(getattr(element, "tag", "") or "")
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _m_attr(element: Any, name: str, default: str | None = None) -> str | None:
    if element is None:
        return default
    for key in (f"{{{OMML_NS}}}{name}", f"{{{WORD_NS}}}{name}", name):
        val = element.get(key)
        if val is not None:
            return str(val)
    return default


def _prop_val(parent: Any, prop_tag: str, child_tag: str, default: str | None = None) -> tuple[bool, str | None]:
    """Return (present, value) for <m:prop_tag><m:child_tag m:val="..."/></m:prop_tag>."""
    for child in parent:
        if _local(child) == prop_tag:
            for sub in child:
                if _local(sub) == child_tag:
                    return True, _m_attr(sub, "val", "")
            return False, default
    return False, default


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (0x4E00 <= code <= 0x9FFF) or (0x3400 <= code <= 0x4DBF) or (0x3000 <= code <= 0x303F)


def _normalize_math_char(char: str) -> str:
    if char in _SYMBOL_MAP:
        return _SYMBOL_MAP[char]
    code = ord(char)
    if 0x1D400 <= code <= 0x1D7FF:
        folded = unicodedata.normalize("NFKD", char)
        if folded in _SYMBOL_MAP:
            return _SYMBOL_MAP[folded]
        return folded
    return char


def _convert_run_text(raw: str) -> str:
    if not raw:
        return ""
    out: list[str] = []
    cjk_buf: list[str] = []

    def flush_cjk() -> None:
        if cjk_buf:
            text = "".join(cjk_buf)
            out.append(rf"\text{{{text}}}")
            cjk_buf.clear()

    for ch in raw:
        if ch in _SYMBOL_MAP or (0x1D400 <= ord(ch) <= 0x1D7FF):
            flush_cjk()
            mapped = _normalize_math_char(ch)
            if mapped.startswith("\\") and mapped[-1:].isalpha():
                out.append(mapped + " ")
            else:
                out.append(mapped)
        elif _is_cjk(ch):
            cjk_buf.append(ch)
        elif ch == "_":
            flush_cjk()
            out.append(r"\_")
        elif ch == "%":
            flush_cjk()
            out.append(r"\%")
        elif ch in {"{", "}"}:
            flush_cjk()
            out.append("\\" + ch)
        else:
            flush_cjk()
            out.append(ch)
    flush_cjk()
    joined = "".join(out)
    joined = _FUNC_RE.sub(
        lambda m: "\\" + m.group(1) + (" " if m.end() < len(joined) and joined[m.end()].isalpha() else ""), joined
    )
    return re.sub(r"\\([A-Za-z]+) (?=[^A-Za-z])", r"\\\1", joined)


def _child_by_name(element: Any, name: str) -> Any | None:
    for child in element:
        if _local(child) == name:
            return child
    return None


def _children_by_name(element: Any, name: str) -> list[Any]:
    return [child for child in element if _local(child) == name]


def _wrap_arg(tex: str) -> str:
    stripped = tex.strip()
    return f"{{{stripped}}}"


def _convert_children(element: Any | None, warnings: list[str], *, in_eqarr: bool = False) -> str:
    if element is None:
        return ""
    parts: list[str] = []
    for child in element:
        tag = _local(child)
        if tag.endswith("Pr") or tag in {"argPr", "ctrlPr"}:
            continue
        piece = _convert_node(child, warnings, in_eqarr=in_eqarr)
        if piece:
            if (
                parts
                and parts[-1][-1:].isalpha()
                and parts[-1].rfind("\\") > parts[-1].rfind("}")
                and piece[:1].isalpha()
            ):
                parts.append(" ")
            parts.append(piece)
    return "".join(parts)


def _convert_delimiter(element: Any, warnings: list[str]) -> str:
    has_beg, beg_raw = _prop_val(element, "dPr", "begChr", "(")
    has_end, end_raw = _prop_val(element, "dPr", "endChr", ")")
    _, sep_raw = _prop_val(element, "dPr", "sepChr", "|")
    beg = beg_raw if has_beg and beg_raw is not None else "("
    end = end_raw if has_end and end_raw is not None else ")"
    sep = sep_raw if sep_raw is not None else "|"
    items = _children_by_name(element, "e")
    if not items:
        return ""

    # Piecewise cases: left '{' with empty right delimiter
    if beg == "{" and end == "":
        if len(items) == 1:
            inner_children = [c for c in items[0] if not _local(c).endswith("Pr")]
            if len(inner_children) == 1 and _local(inner_children[0]) in {"eqArr", "m"}:
                target = inner_children[0]
                if _local(target) == "eqArr":
                    rows = [
                        _convert_children(e, warnings, in_eqarr=True).strip() for e in _children_by_name(target, "e")
                    ]
                else:
                    rows = [
                        " & ".join(_convert_children(e, warnings).strip() for e in _children_by_name(mr, "e"))
                        for mr in _children_by_name(target, "mr")
                    ]
                body = r" \\ ".join(r for r in rows if r)
                return rf"\begin{{cases}} {body} \end{{cases}}"
        if len(items) > 1:
            body = r" \\ ".join(_convert_children(item, warnings, in_eqarr=True).strip() for item in items)
            return rf"\begin{{cases}} {body} \end{{cases}}"

    # Matrix wrapped in standard delimiters -> pmatrix / bmatrix / vmatrix / Vmatrix
    if len(items) == 1:
        inner_children = [c for c in items[0] if not _local(c).endswith("Pr")]
        if len(inner_children) == 1 and _local(inner_children[0]) == "m":
            matrix_map = {
                ("(", ")"): "pmatrix",
                ("[", "]"): "bmatrix",
                ("{", "}"): "Bmatrix",
                ("|", "|"): "vmatrix",
                ("‖", "‖"): "Vmatrix",
                ("∥", "∥"): "Vmatrix",
            }
            env = matrix_map.get((beg, end))
            if env:
                return _convert_matrix(inner_children[0], warnings, env=env)

    rendered_items = [_convert_children(item, warnings).strip() for item in items]
    sep_tex = rf" \middle{_DELIM_LEFT.get(sep, sep)} " if sep in {"|", "‖", "∥"} else f" {sep} "
    inner = sep_tex.join(rendered_items)
    left_tex = _DELIM_LEFT.get(beg)
    right_tex = _DELIM_RIGHT.get(end)
    if left_tex is not None and right_tex is not None:
        if left_tex == "." and right_tex == ".":
            return inner
        return rf"\left{left_tex} {inner} \right{right_tex}".replace(r"\left( ", r"\left(").replace(
            r" \right)", r"\right)"
        )
    return f"{_convert_run_text(beg)}{inner}{_convert_run_text(end)}"


def _convert_matrix(element: Any, warnings: list[str], *, env: str = "matrix") -> str:
    rows: list[str] = []
    for mr in _children_by_name(element, "mr"):
        cells = [_convert_children(cell, warnings).strip() for cell in _children_by_name(mr, "e")]
        rows.append(" & ".join(cells))
    body = r" \\ ".join(rows)
    return rf"\begin{{{env}}} {body} \end{{{env}}}"


def _convert_node(element: Any, warnings: list[str], *, in_eqarr: bool = False) -> str:
    tag = _local(element)
    if tag in {"oMathPara", "oMath", "e", "box", "borderBox", "phant"}:
        return _convert_children(element, warnings, in_eqarr=in_eqarr)
    if tag == "r":
        texts: list[str] = []
        for child in element:
            ctag = _local(child)
            if ctag == "t" and child.text:
                raw = child.text
                if in_eqarr and "&" in raw:
                    segs = [_convert_run_text(seg) for seg in raw.split("&")]
                    texts.append(" & ".join(segs))
                else:
                    texts.append(_convert_run_text(raw.replace("&", "")))
            elif ctag == "br":
                texts.append(r" \\ ")
        return "".join(texts)
    if tag == "f":
        _, ftype = _prop_val(element, "fPr", "type", "bar")
        num = _convert_children(_child_by_name(element, "num"), warnings).strip()
        den = _convert_children(_child_by_name(element, "den"), warnings).strip()
        if ftype == "noBar":
            return rf"\binom{{{num}}}{{{den}}}"
        if ftype in {"lin", "skw"}:
            return f"{{{num}}}/{{{den}}}"
        return rf"\frac{{{num}}}{{{den}}}"
    if tag == "rad":
        _, deg_hide = _prop_val(element, "radPr", "degHide", "0")
        deg = _convert_children(_child_by_name(element, "deg"), warnings).strip()
        body = _convert_children(_child_by_name(element, "e"), warnings).strip()
        if deg_hide in {"1", "true", "on"} or not deg:
            return rf"\sqrt{{{body}}}"
        return rf"\sqrt[{deg}]{{{body}}}"
    if tag == "sSup":
        base = _convert_children(_child_by_name(element, "e"), warnings).strip()
        sup = _convert_children(_child_by_name(element, "sup"), warnings).strip()
        base_wrapped = f"{{{base}}}" if len(base) > 1 and not base.startswith("\\") else base
        return f"{base_wrapped}^{{{sup}}}" if sup else base
    if tag == "sSub":
        base = _convert_children(_child_by_name(element, "e"), warnings).strip()
        sub = _convert_children(_child_by_name(element, "sub"), warnings).strip()
        base_wrapped = f"{{{base}}}" if len(base) > 1 and not base.startswith("\\") else base
        return f"{base_wrapped}_{{{sub}}}" if sub else base
    if tag == "sSubSup":
        base = _convert_children(_child_by_name(element, "e"), warnings).strip()
        sub = _convert_children(_child_by_name(element, "sub"), warnings).strip()
        sup = _convert_children(_child_by_name(element, "sup"), warnings).strip()
        base_wrapped = f"{{{base}}}" if len(base) > 1 and not base.startswith("\\") else base
        return f"{base_wrapped}_{{{sub}}}^{{{sup}}}"
    if tag == "sPre":
        sub = _convert_children(_child_by_name(element, "sub"), warnings).strip()
        sup = _convert_children(_child_by_name(element, "sup"), warnings).strip()
        base = _convert_children(_child_by_name(element, "e"), warnings).strip()
        return f"{{}}_{{{sub}}}^{{{sup}}}{base}"
    if tag == "d":
        return _convert_delimiter(element, warnings)
    if tag == "eqArr":
        rows = [_convert_children(e, warnings, in_eqarr=True).strip() for e in _children_by_name(element, "e")]
        rows = [r for r in rows if r]
        if len(rows) <= 1:
            return rows[0] if rows else ""
        return rf"\begin{{aligned}} {r' \\ '.join(rows)} \end{{aligned}}"
    if tag == "m":
        return _convert_matrix(element, warnings)
    if tag == "nary":
        has_chr, chr_val = _prop_val(element, "naryPr", "chr", "∫")
        _, sub_hide = _prop_val(element, "naryPr", "subHide", "0")
        _, sup_hide = _prop_val(element, "naryPr", "supHide", "0")
        op_char = chr_val if has_chr and chr_val else "∫"
        op_tex = _NARY_MAP.get(op_char, _convert_run_text(op_char) or r"\int")
        sub = _convert_children(_child_by_name(element, "sub"), warnings).strip()
        sup = _convert_children(_child_by_name(element, "sup"), warnings).strip()
        body = _convert_children(_child_by_name(element, "e"), warnings).strip()
        res = op_tex
        if sub and sub_hide not in {"1", "true", "on"}:
            res += f"_{{{sub}}}"
        if sup and sup_hide not in {"1", "true", "on"}:
            res += f"^{{{sup}}}"
        return f"{res} {body}".strip()
    if tag == "limLow":
        base = _convert_children(_child_by_name(element, "e"), warnings).strip()
        lim = _convert_children(_child_by_name(element, "lim"), warnings).strip()
        if base in {r"\lim", "lim", r"\max", "max", r"\min", "min", r"\sup", "sup", r"\inf", "inf"}:
            cmd = base if base.startswith("\\") else f"\\{base}"
            return f"{cmd}_{{{lim}}}"
        return rf"\underset{{{lim}}}{{{base}}}"
    if tag == "limUpp":
        base = _convert_children(_child_by_name(element, "e"), warnings).strip()
        lim = _convert_children(_child_by_name(element, "lim"), warnings).strip()
        return rf"\overset{{{lim}}}{{{base}}}"
    if tag == "acc":
        has_chr, chr_val = _prop_val(element, "accPr", "chr", "\u0302")
        ch = chr_val if has_chr and chr_val else "\u0302"
        body = _convert_children(_child_by_name(element, "e"), warnings).strip()
        multi = len(re.sub(r"\\[A-Za-z]+", "X", body)) > 1
        if ch in {"\u0302", "^", "ˆ"}:
            cmd = r"\widehat" if multi else r"\hat"
        elif ch in {"\u0304", "\u0305", "¯", "￣", "-"}:
            cmd = r"\overline" if multi else r"\bar"
        elif ch in {"\u20d7", "→", "⟶", "\u20d6"}:
            cmd = r"\overrightarrow" if multi else r"\vec"
        elif ch in {"\u0303", "~", "˜"}:
            cmd = r"\widetilde" if multi else r"\tilde"
        elif ch in {"\u0307", "˙"}:
            cmd = r"\dot"
        elif ch in {"\u0308", "¨"}:
            cmd = r"\ddot"
        elif ch in {"\u0311", "\u0361", "︵", "⌒", "\u23dc"}:
            cmd = r"\overgroup"
        else:
            cmd = r"\hat"
        return f"{cmd}{_wrap_arg(body)}"
    if tag == "bar":
        _, pos = _prop_val(element, "barPr", "pos", "top")
        body = _convert_children(_child_by_name(element, "e"), warnings).strip()
        cmd = r"\underline" if pos == "bot" else r"\overline"
        return f"{cmd}{_wrap_arg(body)}"
    if tag == "groupChr":
        has_chr, chr_val = _prop_val(element, "groupChrPr", "chr", "⏟")
        _, pos = _prop_val(element, "groupChrPr", "pos", "bot")
        ch = chr_val if has_chr and chr_val else ("⏞" if pos == "top" else "⏟")
        body = _convert_children(_child_by_name(element, "e"), warnings).strip()
        if ch in {"︵", "⌒", "\u23dc", "\u0311", "\u0361"}:
            return rf"\overgroup{{{body}}}"
        if ch in {"→", "⟶"}:
            return rf"\overrightarrow{{{body}}}"
        if pos == "top" or ch in {"⏞"}:
            return rf"\overbrace{{{body}}}"
        return rf"\underbrace{{{body}}}"
    if tag == "func":
        fname = _convert_children(_child_by_name(element, "fName"), warnings).strip()
        arg = _convert_children(_child_by_name(element, "e"), warnings).strip()
        if not arg:
            return fname
        sep = (
            " "
            if fname[-1:].isalpha() and arg[:1].isalpha()
            else (" " if fname.startswith("\\") and not arg.startswith(("(", "[", "{", "\\left")) else "")
        )
        return f"{fname}{sep}{arg}"
    if tag.endswith("Pr") or tag in {"bookmarkStart", "bookmarkEnd", "proofErr"}:
        return ""
    inner = _convert_children(element, warnings, in_eqarr=in_eqarr)
    if not inner:
        warnings.append(f"unsupported_omml:{tag}")
    return inner


def omml_to_latex(element: Any) -> tuple[str, list[str]]:
    """Convert an lxml OMML element (<m:oMath> or <m:oMathPara>) into clean LaTeX."""
    warnings: list[str] = []
    latex = _convert_node(element, warnings)
    latex = re.sub(r"[ \t]{2,}", " ", latex).strip()
    return latex, warnings


def omml_xml_to_latex(xml_text: str) -> tuple[str, list[str]]:
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False)
    root = etree.fromstring(xml_text.encode("utf-8"), parser)
    return omml_to_latex(root)

"""Extract and convert MathType / Microsoft Equation 3.0 OLE objects (MTEF v3/v5) to LaTeX."""

from __future__ import annotations

import re
import struct

from .word_omml import _SYMBOL_MAP, _convert_run_text

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_FREESECT = 0xFFFFFFFF
_ENDOFCHAIN = 0xFFFFFFFE

_SYMBOL_FONT_MAP: dict[int, str] = {
    0x44: r"\Delta",
    0x47: r"\Gamma",
    0x4C: r"\Lambda",
    0x50: r"\Pi",
    0x51: r"\Theta",
    0x53: r"\Sigma",
    0x55: r"\Upsilon",
    0x57: r"\Omega",
    0x58: r"\Xi",
    0x59: r"\Psi",
    0x61: r"\alpha",
    0x62: r"\beta",
    0x63: r"\chi",
    0x64: r"\delta",
    0x65: r"\varepsilon",
    0x66: r"\varphi",
    0x67: r"\gamma",
    0x68: r"\eta",
    0x69: r"\iota",
    0x6A: r"\phi",
    0x6B: r"\kappa",
    0x6C: r"\lambda",
    0x6D: r"\mu",
    0x6E: r"\nu",
    0x6F: "o",
    0x70: r"\pi",
    0x71: r"\theta",
    0x72: r"\rho",
    0x73: r"\sigma",
    0x74: r"\tau",
    0x75: r"\upsilon",
    0x77: r"\omega",
    0x78: r"\xi",
    0x79: r"\psi",
    0x7A: r"\zeta",
    0xA2: "'",
    0xA3: r"\leqslant",
    0xA5: r"\infty",
    0xB0: r"^{\circ}",
    0xB1: r"\pm",
    0xB2: "''",
    0xB3: r"\geqslant",
    0xB4: r"\times",
    0xB5: r"\propto",
    0xB6: r"\partial",
    0xB7: r"\cdot",
    0xB8: r"\div",
    0xB9: r"\neq",
    0xBA: r"\equiv",
    0xBB: r"\approx",
    0xBC: r"\dots",
    0xC5: r"\oplus",
    0xC6: r"\varnothing",
    0xC7: r"\cap",
    0xC8: r"\cup",
    0xC9: r"\supset",
    0xCA: r"\supseteq",
    0xCB: r"\not\subset",
    0xCC: r"\subset",
    0xCD: r"\subseteq",
    0xCE: r"\in",
    0xCF: r"\notin",
    0xD0: r"\angle",
    0xD1: r"\nabla",
    0xD7: r"\cdot",
    0xD9: r"\wedge",
    0xDA: r"\vee",
    0xDB: r"\Leftrightarrow",
    0xDC: r"\Leftarrow",
    0xDE: r"\Rightarrow",
    0xAC: r"\leftarrow",
    0xAE: r"\to",
    0xAB: r"\leftrightarrow",
    0x22: r"\forall",
    0x24: r"\exists",
    0x5E: r"\perp",
    0xD6: r"\sqrt",
    0xE5: r"\sum",
    0xD5: r"\prod",
    0xF2: r"\int",
}

_MTEXTRA_MAP: dict[int, str] = {
    0x43: r"\cdots",
    0x4C: r"\ell",
    0x4F: r"\cdots",
    0x66: r"\overgroup",
    0x71: r"\triangle",
    0x72: r"\angle",
    0x7B: r"\parallel",
}

_EMBELL_MAP: dict[int, str] = {
    2: r"\dot",
    3: r"\ddot",
    4: r"\dddot",
    5: "'",
    6: "''",
    7: r"\backprime",
    8: r"\tilde",
    9: r"\hat",
    11: r"\vec",
    16: r"\bar",
    17: r"\underline",
    20: r"\overgroup",
    29: "'''",
}


def extract_ole_streams(data: bytes) -> dict[str, bytes]:
    """Extract named streams from an OLE2 Compound File Binary Format buffer."""
    if len(data) < 512 or data[:8] != _OLE_MAGIC:
        return {}
    try:
        sector_shift = struct.unpack_from("<H", data, 0x1E)[0]
        mini_shift = struct.unpack_from("<H", data, 0x20)[0]
        if sector_shift not in {9, 12} or mini_shift != 6:
            return {}
        sector_size = 1 << sector_shift
        mini_size = 1 << mini_shift
        first_dir_sect = struct.unpack_from("<I", data, 0x30)[0]
        mini_cutoff = struct.unpack_from("<I", data, 0x38)[0]
        first_minifat_sect = struct.unpack_from("<I", data, 0x3C)[0]
        num_minifat_sects = struct.unpack_from("<I", data, 0x40)[0]
        first_difat_sect = struct.unpack_from("<I", data, 0x44)[0]
        num_difat_sects = struct.unpack_from("<I", data, 0x48)[0]

        def sector_bytes(sec_id: int) -> bytes:
            offset = (sec_id + 1) * sector_size
            if offset < 0 or offset + sector_size > len(data):
                return b""
            return data[offset : offset + sector_size]

        difat = list(struct.unpack_from("<109I", data, 0x4C))
        cur_difat = first_difat_sect
        for _ in range(min(num_difat_sects, 1024)):
            if cur_difat >= _ENDOFCHAIN:
                break
            chunk = sector_bytes(cur_difat)
            if len(chunk) < sector_size:
                break
            entries = struct.unpack(f"<{sector_size // 4}I", chunk)
            difat.extend(entries[:-1])
            cur_difat = entries[-1]

        fat: list[int] = []
        for sec_id in difat:
            if sec_id >= _ENDOFCHAIN:
                continue
            chunk = sector_bytes(sec_id)
            if len(chunk) == sector_size:
                fat.extend(struct.unpack(f"<{sector_size // 4}I", chunk))

        def read_chain(start_sec: int, max_bytes: int | None = None) -> bytes:
            out = bytearray()
            cur = start_sec
            seen: set[int] = set()
            while cur < _ENDOFCHAIN and cur not in seen and cur < len(fat):
                seen.add(cur)
                block = sector_bytes(cur)
                if not block:
                    break
                out.extend(block)
                if max_bytes is not None and len(out) >= max_bytes:
                    return bytes(out[:max_bytes])
                cur = fat[cur]
            return bytes(out)

        dir_bytes = read_chain(first_dir_sect)
        minifat_bytes = (
            read_chain(first_minifat_sect) if num_minifat_sects and first_minifat_sect < _ENDOFCHAIN else b""
        )
        minifat = list(struct.unpack(f"<{len(minifat_bytes) // 4}I", minifat_bytes)) if len(minifat_bytes) >= 4 else []

        root_stream = b""
        if len(dir_bytes) >= 128:
            root_start = struct.unpack_from("<I", dir_bytes, 0x74)[0]
            root_size = (
                struct.unpack_from("<Q", dir_bytes, 0x78)[0]
                if sector_shift == 12
                else struct.unpack_from("<I", dir_bytes, 0x78)[0]
            )
            if root_start < _ENDOFCHAIN and root_size > 0:
                root_stream = read_chain(root_start, int(root_size))

        def read_mini_chain(start_sec: int, size: int) -> bytes:
            out = bytearray()
            cur = start_sec
            seen: set[int] = set()
            while cur < _ENDOFCHAIN and cur not in seen and len(out) < size:
                seen.add(cur)
                off = cur * mini_size
                if off + mini_size > len(root_stream):
                    break
                out.extend(root_stream[off : off + mini_size])
                cur = minifat[cur] if cur < len(minifat) else _ENDOFCHAIN
            return bytes(out[:size])

        streams: dict[str, bytes] = {}
        for pos in range(0, len(dir_bytes) - 127, 128):
            entry = dir_bytes[pos : pos + 128]
            name_len = struct.unpack_from("<H", entry, 0x40)[0]
            obj_type = entry[0x42]
            if obj_type != 2 or name_len < 2 or name_len > 64:
                continue
            name = entry[: name_len - 2].decode("utf-16-le", errors="ignore")
            start_sec = struct.unpack_from("<I", entry, 0x74)[0]
            size = (
                struct.unpack_from("<Q", entry, 0x78)[0]
                if sector_shift == 12
                else struct.unpack_from("<I", entry, 0x78)[0]
            )
            if size == 0 or size > 32 * 1024 * 1024:
                streams[name] = b""
            elif size < mini_cutoff and root_stream:
                streams[name] = read_mini_chain(start_sec, int(size))
            else:
                streams[name] = read_chain(start_sec, int(size))
        return streams
    except Exception:
        return {}


class _MtefError(ValueError):
    pass


class _MtefReader:
    def __init__(self, buf: bytes, version: int) -> None:
        self.buf = buf
        self.pos = 0
        self.version = version
        self.fonts: dict[int, str] = {}

    def u8(self) -> int:
        if self.pos >= len(self.buf):
            raise _MtefError("eof")
        val = self.buf[self.pos]
        self.pos += 1
        return val

    def u16(self) -> int:
        if self.pos + 2 > len(self.buf):
            raise _MtefError("eof16")
        val = struct.unpack_from("<H", self.buf, self.pos)[0]
        self.pos += 2
        return val

    def uint_v5(self) -> int:
        first = self.u8()
        if first < 0xFF:
            return first
        return self.u16()

    def sint_v5(self) -> int:
        return self.uint_v5() - 128

    def cstr(self) -> str:
        end = self.buf.find(b"\x00", self.pos)
        if end < 0:
            raise _MtefError("unterminated string")
        raw = self.buf[self.pos : end]
        self.pos = end + 1
        return raw.decode("latin1", errors="ignore")

    def skip_nudge(self) -> None:
        b1 = self.u8()
        b2 = self.u8()
        if b1 == 128 and b2 == 128:
            self.pos += 4

    def skip_ruler(self) -> None:
        n_stops = self.u8()
        for _ in range(n_stops):
            self.u8()
            self.u16()

    def read_object_list(self) -> list[str]:
        items: list[str] = []
        while self.pos < len(self.buf):
            rec_byte = self.u8()
            rec_type = rec_byte & 0x0F if self.version == 3 else rec_byte
            opts = (rec_byte >> 4) if self.version == 3 else 0
            if rec_type == 0:
                break
            piece = self._dispatch(rec_type, opts)
            if piece:
                items.append(piece)
        return items

    def _join_tokens(self, tokens: list[str]) -> str:
        out: list[str] = []
        for tok in tokens:
            if out and out[-1][-1:].isalpha() and out[-1].rfind("\\") > out[-1].rfind("}") and tok[:1].isalpha():
                out.append(" ")
            out.append(tok)
        return "".join(out)

    def read_slot(self) -> str | None:
        """Read one template/pile/matrix child record (usually LINE)."""
        while self.pos < len(self.buf):
            rec_byte = self.u8()
            rec_type = rec_byte & 0x0F if self.version == 3 else rec_byte
            opts = (rec_byte >> 4) if self.version == 3 else 0
            if rec_type == 0:
                return None
            if rec_type in {8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20}:
                self._dispatch(rec_type, opts)
                continue
            return self._dispatch(rec_type, opts)
        return None

    def _dispatch(self, rec_type: int, v3_opts: int) -> str:
        opts = v3_opts if self.version == 3 else (self.u8() if rec_type in {1, 2, 3, 4, 5, 6} else 0)
        if rec_type == 1:
            return self._read_line(opts)
        if rec_type == 2:
            return self._read_char(opts)
        if rec_type == 3:
            return self._read_tmpl(opts)
        if rec_type == 4:
            return self._read_pile(opts)
        if rec_type == 5:
            return self._read_matrix(opts)
        if rec_type == 6:
            return self._read_embell(opts)
        if rec_type == 7:
            self.skip_ruler()
            return ""
        if rec_type == 8:
            if self.version == 5:
                self.uint_v5()
                self.u8()
            else:
                self.u8()
                self.cstr()
            return ""
        if rec_type == 9:
            lsize = self.u8()
            if lsize == 100:
                self.pos += 2 if self.version == 3 else 0
                if self.version == 5:
                    self.sint_v5()
            elif lsize == 101:
                self.pos += 2
            else:
                if self.version == 5:
                    self.sint_v5()
                else:
                    self.u8()
            return ""
        if rec_type in {10, 11, 12, 13, 14, 15, 16}:
            return ""
        if rec_type == 17:
            enc = self.uint_v5()
            name = self.cstr()
            self.fonts[len(self.fonts) + 1] = f"{name}:{enc}"
            return ""
        if rec_type == 18:
            self.uint_v5()
            return ""
        if rec_type == 19:
            c_opts = self.u8()
            count = 4 if (c_opts & 0x01) else 3
            self.pos += count * 2
            if c_opts & 0x04:
                self.cstr()
            return ""
        if rec_type == 20:
            self.cstr()
            return ""
        if rec_type >= 100:
            length = self.uint_v5()
            self.pos += length
            return ""
        raise _MtefError(f"unknown MTEF record {rec_type}")

    def _read_line(self, opts: int) -> str:
        if opts & 0x08:
            self.skip_nudge()
        if opts & 0x04:
            self.pos += 2
        if opts & 0x02:
            if self.version == 3:
                self.skip_ruler()
            else:
                rec = self.u8()
                if rec == 7:
                    self.skip_ruler()
        if opts & 0x01:
            return ""
        return self._join_tokens(self.read_object_list())

    def _read_embell(self, opts: int) -> str:
        if opts & 0x08:
            self.skip_nudge()
        emb = self.u8()
        return _EMBELL_MAP.get(emb, "")

    def _read_char(self, opts: int) -> str:
        if opts & 0x08:
            self.skip_nudge()
        typeface = self.sint_v5() if self.version == 5 else (self.u8() - 128)
        mtcode = self.u16() if (self.version == 5 and not (opts & 0x02)) else 0
        char8 = 0
        if self.version == 3:
            mtcode = self.u16()
        elif not (opts & 0x04):
            if opts & 0x10:
                char8 = self.u8()
            if opts & 0x20:
                mtcode = self.u16() or mtcode
        embells: list[str] = []
        if opts & 0x01:
            embells = self.read_object_list()

        tex = self._decode_char(typeface, mtcode, char8)
        for emb in embells:
            if emb in {"'", "''", "'''"}:
                tex = f"{tex}{emb}"
            elif emb.startswith("\\"):
                tex = f"{emb}{{{tex}}}"
        return tex

    def _decode_char(self, typeface: int, mtcode: int, char8: int) -> str:
        # MathType typefaces: 4=LC Greek, 5=UC Greek, 6=Symbol, 11=MT Extra
        code = char8 or mtcode
        if typeface in {4, 5, 6} and 0 < code <= 0xFF and code in _SYMBOL_FONT_MAP:
            return _SYMBOL_FONT_MAP[code]
        if typeface == 11 and 0 < code <= 0xFF and code in _MTEXTRA_MAP:
            return _MTEXTRA_MAP[code]
        if 0xF000 <= mtcode <= 0xF0FF:
            low = mtcode & 0xFF
            if low in _SYMBOL_FONT_MAP:
                return _SYMBOL_FONT_MAP[low]
        if mtcode > 0:
            ch = chr(mtcode)
            if ch in _SYMBOL_MAP:
                return _SYMBOL_MAP[ch]
            if ch == " ":
                return r"\,"
            if ch.isprintable():
                return _convert_run_text(ch)
        if 32 <= char8 <= 126:
            return _convert_run_text(chr(char8))
        return ""

    def _read_pile(self, opts: int) -> str:
        if opts & 0x08:
            self.skip_nudge()
        self.u8()  # halign
        self.u8()  # valign
        if opts & 0x02:
            if self.version == 3:
                self.skip_ruler()
            else:
                rec = self.u8()
                if rec == 7:
                    self.skip_ruler()
        lines = self.read_object_list()
        return r" \\ ".join(s.strip() for s in lines if s.strip())

    def _read_matrix(self, opts: int) -> str:
        if opts & 0x08:
            self.skip_nudge()
        self.u8()  # valign
        self.u8()  # h_just
        self.u8()  # v_just
        rows = self.u8()
        cols = self.u8()
        part_bytes = ((rows + 1) * 2 + 7) // 8 + ((cols + 1) * 2 + 7) // 8
        self.pos += part_bytes
        cells = self.read_object_list()
        if rows <= 0 or cols <= 0:
            return " ".join(cells)
        matrix_rows: list[str] = []
        for r in range(rows):
            row_cells = cells[r * cols : (r + 1) * cols]
            if row_cells:
                matrix_rows.append(" & ".join(row_cells))
        return rf"\begin{{matrix}} {r' \\ '.join(matrix_rows)} \end{{matrix}}"

    def _read_tmpl(self, opts: int) -> str:
        if opts & 0x08:
            self.skip_nudge()
        selector = self.u8()
        variation = self.u8()
        if variation & 0x80 and self.version == 5:
            variation = (variation & 0x7F) | (self.u8() << 7)
        self.u8()  # template-specific options

        slots = self.read_object_list()

        # Fence templates: 0..9
        if selector in {0, 1, 2, 3, 4, 5, 6, 7, 8, 9}:
            body = slots[0] if slots else ""
            has_left = bool(variation & 0x01) or variation == 0
            has_right = bool(variation & 0x02) or variation == 0
            if selector == 2 and has_left and not (variation & 0x02) and r"\\" in body:
                return rf"\begin{{cases}} {body} \end{{cases}}"
            fences = {
                0: (r"\langle", r"\rangle"),
                1: ("(", ")"),
                2: (r"\{", r"\}"),
                3: ("[", "]"),
                4: ("|", "|"),
                5: (r"\|", r"\|"),
                6: (r"\lfloor", r"\rfloor"),
                7: (r"\lceil", r"\rceil"),
                8: ("]", "["),
                9: ("(", "]"),
            }
            lch, rch = fences.get(selector, ("(", ")"))
            left = lch if has_left else "."
            right = rch if has_right else "."
            return rf"\left{left} {body} \right{right}".replace(r"\left( ", r"\left(").replace(r" \right)", r"\right)")
        if selector == 10:  # radical
            main = slots[0] if slots else ""
            nth = slots[1] if len(slots) > 1 else ""
            return rf"\sqrt[{nth}]{{{main}}}" if (variation & 0x01 and nth) else rf"\sqrt{{{main}}}"
        if selector == 11:  # fraction
            num = slots[0] if slots else ""
            den = slots[1] if len(slots) > 1 else ""
            return f"{{{num}}}/{{{den}}}" if (variation & 0x02) else rf"\frac{{{num}}}{{{den}}}"
        if selector == 12:
            return rf"\underline{{{slots[0] if slots else ''}}}"
        if selector == 13:
            return rf"\overline{{{slots[0] if slots else ''}}}"
        if selector == 14:  # arrow
            main = slots[0] if slots else ""
            return rf"\xrightarrow{{{main}}}" if main else r"\to"
        if selector in {15, 16, 17, 18, 19, 20, 21, 22}:
            ops = {
                15: r"\int",
                16: r"\sum",
                17: r"\prod",
                18: r"\coprod",
                19: r"\bigcup",
                20: r"\bigcap",
                21: r"\int",
                22: r"\sum",
            }
            op = ops[selector]
            if selector == 15 and (variation & 0x02):
                op = r"\iint"
            elif selector == 15 and (variation & 0x04):
                op = r"\iiint"
            main = slots[0] if slots else ""
            lower = slots[1] if len(slots) > 1 else ""
            upper = slots[2] if len(slots) > 2 else ""
            res = op
            if lower:
                res += f"_{{{lower}}}"
            if upper:
                res += f"^{{{upper}}}"
            return f"{res} {main}".strip()
        if selector == 23:  # limit
            main = slots[0] if slots else r"\lim"
            lower = slots[1] if len(slots) > 1 else ""
            return f"{main}_{{{lower}}}" if lower else main
        if selector in {24, 25}:
            main = slots[0] if slots else ""
            note = slots[1] if len(slots) > 1 else ""
            cmd = r"\overbrace" if (variation & 0x01) else r"\underbrace"
            return (
                f"{cmd}{{{main}}}^{{{note}}}"
                if (note and cmd == r"\overbrace")
                else (f"{cmd}{{{main}}}_{{{note}}}" if note else f"{cmd}{{{main}}}")
            )
        if selector == 27:  # subscript
            return f"_{{{slots[0] if slots else ''}}}"
        if selector == 28:  # superscript
            return f"^{{{slots[0] if slots else ''}}}"
        if selector == 29:  # sub + sup
            sub = slots[0] if slots else ""
            sup = slots[1] if len(slots) > 1 else ""
            return f"_{{{sub}}}^{{{sup}}}"
        if selector == 31:
            return rf"\overrightarrow{{{slots[0] if slots else ''}}}"
        if selector == 32:
            return rf"\widetilde{{{slots[0] if slots else ''}}}"
        if selector == 33:
            return rf"\widehat{{{slots[0] if slots else ''}}}"
        if selector == 34:
            return rf"\overgroup{{{slots[0] if slots else ''}}}"
        if selector in {36, 37}:
            return slots[0] if slots else ""
        raise _MtefError(f"unsupported template {selector}")


def mtef_to_latex(mtef: bytes) -> str | None:
    """Parse raw MTEF v3 or v5 payload into KaTeX-compatible LaTeX, or None if unsupported."""
    if len(mtef) < 6:
        return None
    version = mtef[0]
    if version not in {3, 5}:
        return None
    try:
        reader = _MtefReader(mtef, version)
        reader.pos = 5  # version, platform, product, ver, subver
        if version == 5:
            reader.cstr()  # application key (e.g. "DSMT4")
            reader.u8()  # equation options (inline/display)
        tokens = reader.read_object_list()
        latex = reader._join_tokens(tokens).strip()
        latex = re.sub(r"(\\[A-Za-z]+)\s+(_|\^)", r"\1\2", latex)
        latex = re.sub(r"\s{2,}", " ", latex).strip()
        return latex or None
    except Exception:
        return None


def ole_equation_to_latex(ole_bytes: bytes) -> str | None:
    """Extract MathType / Equation 3.0 OLE binary and convert to LaTeX, or None."""
    streams = extract_ole_streams(ole_bytes)
    if not streams:
        return None
    eq_native = streams.get("Equation Native")
    if eq_native and len(eq_native) >= 30:
        cb_hdr = struct.unpack_from("<H", eq_native, 0)[0]
        if 10 <= cb_hdr < len(eq_native):
            parsed = mtef_to_latex(eq_native[cb_hdr:])
            if parsed:
                return parsed
    # Fallback: scan for embedded MathType TeX comment or MTEF signature if non-standard stream name
    for payload in streams.values():
        if len(payload) >= 8 and payload[0] == 5 and b"DSMT" in payload[:20]:
            parsed = mtef_to_latex(payload)
            if parsed:
                return parsed
    return None

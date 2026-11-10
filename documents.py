"""Comprehensive document reader, writer, and converter for LifeOS Chat.

Supports UYAP (.udf), Word (.docx, .doc), Rich Text (.rtf), OpenDocument (.odt),
PDF (.pdf with scan rendering fallback), Excel (.xlsx, .csv), TIFF (.tif/.tiff),
HTML, Markdown, and plain text.
Ported and adapted from the Folio (evrak convert) document engine.
"""
from __future__ import annotations

import base64
import csv
import html
import io
import json
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


WIN1254_TABLE: dict[int, int] = {
    0x80: 0x20AC, 0x82: 0x201A, 0x83: 0x0192, 0x84: 0x201E,
    0x85: 0x2026, 0x86: 0x2020, 0x87: 0x2021, 0x88: 0x02C6,
    0x89: 0x2030, 0x8A: 0x0160, 0x8B: 0x2039, 0x8C: 0x0152,
    0x91: 0x2018, 0x92: 0x2019, 0x93: 0x201C, 0x94: 0x201D,
    0x95: 0x2022, 0x96: 0x2013, 0x97: 0x2014, 0x98: 0x02DC,
    0x99: 0x2122, 0x9A: 0x0161, 0x9B: 0x203A, 0x9C: 0x0153,
    0x9F: 0x0178,
    0xD0: 0x011E,  # Ğ
    0xDD: 0x0130,  # İ
    0xDE: 0x015E,  # Ş
    0xF0: 0x011F,  # ğ
    0xFD: 0x0131,  # ı
    0xFE: 0x015F,  # ş
}

SUPPORTED_DOC_EXTENSIONS = {
    ".udf", ".pdf", ".docx", ".doc", ".rtf", ".odt",
    ".xlsx", ".csv", ".txt", ".md", ".html", ".htm",
    ".json", ".xml", ".yaml", ".yml", ".py", ".dart", ".js", ".ts", ".tsx", ".jsx",
    ".qml", ".cpp", ".c", ".h", ".rs", ".go", ".java", ".kt", ".sh", ".toml", ".sql", ".css", ".ini", ".log",
}

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp"}
SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"}
EXPORT_FORMATS = ("udf", "docx", "pdf", "rtf", "md", "txt", "html")


@dataclass
class DocSpan:
    start: int
    length: int
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    font_family: str = "Times New Roman"
    font_size: float = 12.0
    color: str | None = None


@dataclass
class DocBlock:
    kind: str = "paragraph"  # paragraph | heading | list | table | code
    text: str = ""
    level: int = 0
    ordered: bool = False
    alignment: int = 0  # 0=left, 1=center, 2=right, 3=justify
    spans: list[DocSpan] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class ExtractedDocument:
    path: str
    name: str
    format_label: str
    text: str
    page_count: int = 1
    char_count: int = 0
    word_count: int = 0
    signed: bool = False
    signers: list[str] = field(default_factory=list)
    rendered_pages: list[str] = field(default_factory=list)  # data URLs for scanned PDFs/TIFFs
    summary_badge: str = ""
    truncated: bool = False

    def to_meta_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "format": self.format_label,
            "pages": self.page_count,
            "chars": self.char_count,
            "words": self.word_count,
            "signed": self.signed,
            "signers": self.signers,
            "badge": self.summary_badge,
            "truncated": self.truncated,
            "scanned": bool(self.rendered_pages),
        }


def decode_win1254(data: bytes | bytearray) -> str:
    return "".join(chr(WIN1254_TABLE.get(b, b)) for b in data)


def smart_decode(data: bytes) -> str:
    if len(data) >= 2 and data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        encoding = "utf-16-le" if data[0] == 0xFF else "utf-16-be"
        return data[2:].decode(encoding, errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return decode_win1254(data)


def _decode_udf_rtf_escapes(raw: str) -> tuple[str, list[int]]:
    if "\\'" not in raw:
        return raw, list(range(len(raw) + 1))
    out: list[str] = []
    mapping = [0] * (len(raw) + 1)
    i = 0
    pos = 0
    n = len(raw)
    while i < n:
        mapping[i] = pos
        if i + 3 < n and raw[i] == "\\" and raw[i + 1] == "'":
            hex_part = raw[i + 2 : i + 4]
            try:
                byte_val = int(hex_part, 16)
                out.append(chr(WIN1254_TABLE.get(byte_val, byte_val)))
                mapping[i + 1] = pos
                mapping[i + 2] = pos
                mapping[i + 3] = pos
                i += 4
                pos += 1
                continue
            except ValueError:
                pass
        out.append(raw[i])
        i += 1
        pos += 1
    mapping[n] = pos
    return "".join(out), mapping


def _read_zip_from_local_headers(data: bytes) -> dict[str, bytes]:
    """Fallback ZIP reader for UYAP UDF archives with streaming data descriptors."""
    result: dict[str, bytes] = {}
    offset = 0
    n = len(data)
    while offset + 30 < n:
        if data[offset : offset + 4] != b"PK\x03\x04":
            break
        flags = struct.unpack_from("<H", data, offset + 6)[0]
        method = struct.unpack_from("<H", data, offset + 8)[0]
        comp_size = struct.unpack_from("<I", data, offset + 18)[0]
        name_len = struct.unpack_from("<H", data, offset + 26)[0]
        extra_len = struct.unpack_from("<H", data, offset + 28)[0]
        fname = data[offset + 30 : offset + 30 + name_len].decode("utf-8", errors="replace")
        data_start = offset + 30 + name_len + extra_len
        has_dd = (flags & 0x08) != 0

        if comp_size == 0 and has_dd:
            search = data_start
            while search < n - 4:
                if data[search : search + 4] in (b"PK\x03\x04", b"PK\x07\x08", b"PK\x01\x02"):
                    break
                search += 1
            comp_size = search - data_start
            if data[search : search + 4] == b"PK\x07\x08":
                offset = search + 16
            else:
                offset = search
        else:
            offset = data_start + comp_size

        if data_start + comp_size > n:
            break
        chunk = data[data_start : data_start + comp_size]
        try:
            if method == 8:
                raw_file = zlib.decompress(chunk, -15)
            elif method == 0:
                raw_file = chunk
            else:
                continue
            result[fname.lower()] = raw_file
        except Exception:
            continue
    return result


def parse_udf_signature(sig_bytes: bytes) -> list[str]:
    """Extracts signer names from UYAP sign.sgn (CMS/PKCS#7 SignedData)."""
    if not sig_bytes:
        return []
    signers: list[str] = []
    try:
        from cryptography.hazmat.primitives.serialization import pkcs7
        from cryptography.x509.oid import NameOID

        certs = pkcs7.load_der_pkcs7_certificates(sig_bytes)
        for cert in certs:
            cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            if not cn_attrs:
                continue
            cn = str(cn_attrs[0].value).strip()
            # Filter out root/intermediate CA names
            if any(k in cn.upper() for k in ("KOK ", "KÖK ", "ALT ", "SUB ", "CA ", "SERTİFİKA HİZMET", "SERTIFIKA HIZMET", "TUBITAK", "TÜBİTAK", "E-TUGRA", "E-GUVEN", "TURKTRUST")):
                continue
            org_attrs = cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
            org = str(org_attrs[0].value).strip() if org_attrs else ""
            label = f"{cn} ({org})" if org and org != cn else cn
            if label not in signers:
                signers.append(label)
    except Exception:
        pass
    return signers


def read_udf_bytes(data: bytes) -> tuple[str, bool, list[str], int]:
    """Reads a UYAP .udf document and returns (formatted_text, signed, signers, est_pages)."""
    xml_bytes: bytes | None = None
    sig_bytes: bytes | None = None

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                low = info.filename.lower()
                if low == "content.xml":
                    xml_bytes = zf.read(info.filename)
                elif low == "sign.sgn":
                    sig_bytes = zf.read(info.filename)
    except Exception:
        pass

    if xml_bytes is None:
        recovered = _read_zip_from_local_headers(data)
        xml_bytes = recovered.get("content.xml")
        if sig_bytes is None:
            sig_bytes = recovered.get("sign.sgn")

    if xml_bytes is None:
        # Check if the file itself is raw UDF XML
        if b"<template" in data[:1024]:
            xml_bytes = data
        else:
            raise ValueError("Geçerli bir UYAP (.udf) dosyası değil veya content.xml bulunamadı.")

    xml_text = smart_decode(xml_bytes)
    root = ET.fromstring(xml_text)

    content_el = root.find("content")
    raw_text = content_el.text if content_el is not None and content_el.text else ""
    decoded_text, offset_map = _decode_udf_rtf_escapes(raw_text)

    # Read template <data> fields
    fields: dict[str, str] = {}
    for data_el in root.findall("data"):
        for field_el in data_el:
            tag = field_el.tag.split("}")[-1]
            val = (field_el.text or "").strip()
            if val and not fields.get(tag):
                fields[tag] = val

    def extract_para(para_el: ET.Element) -> str:
        parts: list[str] = []
        for child in para_el:
            tag = child.tag.split("}")[-1]
            if tag not in ("content", "space", "tab", "field"):
                continue
            start = int(child.attrib.get("startOffset", "-1"))
            length = int(child.attrib.get("length", "0"))
            if start < 0 or length < 0 or start + length > len(raw_text):
                continue
            piece = decoded_text[offset_map[start] : offset_map[start + length]]
            if tag == "field":
                fname = child.attrib.get("fieldName", "")
                if fname in fields and piece.strip() == fname:
                    piece = fields[fname]
            parts.append(piece)
        line = "".join(parts)
        if line.endswith("\n"):
            line = line[:-1]
        if para_el.attrib.get("Bulleted") == "true" and line.strip():
            return f"• {line}"
        if para_el.attrib.get("Numbered") == "true" and line.strip():
            return f"1. {line}"
        return line

    def extract_container(container: ET.Element) -> list[str]:
        lines: list[str] = []
        for el in container:
            tag = el.tag.split("}")[-1]
            if tag == "paragraph":
                lines.append(extract_para(el))
            elif tag == "table":
                table_rows: list[list[str]] = []
                for row_el in el:
                    if row_el.tag.split("}")[-1] != "row":
                        continue
                    row_cells: list[str] = []
                    for cell_el in row_el:
                        if cell_el.tag.split("}")[-1] != "cell":
                            continue
                        cell_lines = [x.strip() for x in extract_container(cell_el) if x.strip()]
                        row_cells.append(" ".join(cell_lines).replace("|", "\\|"))
                    if row_cells:
                        table_rows.append(row_cells)
                if table_rows:
                    col_count = max(len(r) for r in table_rows)
                    for idx, r in enumerate(table_rows):
                        padded = r + [""] * (col_count - len(r))
                        lines.append("| " + " | ".join(padded) + " |")
                        if idx == 0:
                            lines.append("| " + " | ".join(["---"] * col_count) + " |")
                    lines.append("")
            elif tag in ("body", "section", "header", "footer"):
                lines.extend(extract_container(el))
        return lines

    elements_el = root.find("elements")
    if elements_el is not None:
        body_lines = extract_container(elements_el)
        final_text = "\n".join(body_lines).strip()
    else:
        final_text = decoded_text.strip()

    if not final_text and decoded_text.strip():
        final_text = decoded_text.strip()

    signed = sig_bytes is not None and len(sig_bytes) > 0
    signers = parse_udf_signature(sig_bytes) if signed and sig_bytes else []
    est_pages = max(1, (len(final_text) + 2499) // 2500)
    return final_text, signed, signers, est_pages


def read_docx_bytes(data: bytes) -> tuple[str, int]:
    """Reads .docx paragraphs, headers, footers, and tables into clean Markdown text."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        if "word/document.xml" not in names:
            raise ValueError("Geçerli bir DOCX dosyası değil (word/document.xml eksik).")
        doc_xml = zf.read("word/document.xml")

    root = ET.fromstring(doc_xml)
    lines: list[str] = []

    def para_text(p_el: ET.Element) -> str:
        chunks: list[str] = []
        for node in p_el.iter():
            tag = node.tag.split("}")[-1]
            if tag == "t" and node.text:
                chunks.append(node.text)
            elif tag == "tab":
                chunks.append("\t")
            elif tag in ("br", "cr"):
                chunks.append("\n")
        return "".join(chunks)

    body = next((el for el in root if el.tag.endswith("}body") or el.tag == "body"), root)
    for child in body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            txt = para_text(child)
            if txt.strip():
                lines.append(txt)
            elif lines and lines[-1] != "":
                lines.append("")
        elif tag == "tbl":
            rows: list[list[str]] = []
            for tr in child:
                if tr.tag.split("}")[-1] != "tr":
                    continue
                row_cells: list[str] = []
                for tc in tr:
                    if tc.tag.split("}")[-1] != "tc":
                        continue
                    cell_paras = [para_text(p).strip() for p in tc if p.tag.split("}")[-1] == "p"]
                    row_cells.append(" ".join(x for x in cell_paras if x).replace("|", "\\|"))
                if row_cells:
                    rows.append(row_cells)
            if rows:
                cols = max(len(r) for r in rows)
                for idx, r in enumerate(rows):
                    padded = r + [""] * (cols - len(r))
                    lines.append("| " + " | ".join(padded) + " |")
                    if idx == 0:
                        lines.append("| " + " | ".join(["---"] * cols) + " |")
                lines.append("")

    full = "\n".join(lines).strip()
    pages = max(1, (len(full) + 2499) // 2500)
    return full, pages


def read_odt_bytes(data: bytes) -> tuple[str, int]:
    """Reads OpenDocument Text (.odt) files."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml_bytes = zf.read("content.xml")
    root = ET.fromstring(xml_bytes)
    lines: list[str] = []
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag in ("p", "h"):
            txt = "".join(node.itertext()).strip()
            if txt:
                lines.append(txt)
    full = "\n".join(lines).strip()
    return full, max(1, (len(full) + 2499) // 2500)


def read_xlsx_bytes(data: bytes) -> tuple[str, int]:
    """Reads Excel (.xlsx) workbooks into Markdown tables per sheet."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        parts = {name: zf.read(name) for name in zf.namelist()}
    if "xl/workbook.xml" not in parts:
        raise ValueError("Geçerli bir XLSX dosyası değil.")

    shared: list[str] = []
    if "xl/sharedStrings.xml" in parts:
        ss_root = ET.fromstring(parts["xl/sharedStrings.xml"])
        for si in ss_root.iter():
            if si.tag.split("}")[-1] == "si":
                shared.append("".join(t.text or "" for t in si.iter() if t.tag.split("}")[-1] == "t"))

    wb_root = ET.fromstring(parts["xl/workbook.xml"])
    sheet_names: list[str] = []
    for el in wb_root.iter():
        if el.tag.split("}")[-1] == "sheet":
            sheet_names.append(el.attrib.get("name", f"Sayfa {len(sheet_names)+1}"))

    sheet_files = sorted(k for k in parts if k.startswith("xl/worksheets/sheet") and k.endswith(".xml"))
    out_sections: list[str] = []

    for idx, sfile in enumerate(sheet_files):
        sname = sheet_names[idx] if idx < len(sheet_names) else f"Sayfa {idx+1}"
        s_root = ET.fromstring(parts[sfile])
        rows_data: list[list[str]] = []
        for row_el in s_root.iter():
            if row_el.tag.split("}")[-1] != "row":
                continue
            row_vals: list[str] = []
            for c_el in row_el:
                if c_el.tag.split("}")[-1] != "c":
                    continue
                c_type = c_el.attrib.get("t", "")
                v_el = next((x for x in c_el if x.tag.split("}")[-1] == "v"), None)
                is_el = next((x for x in c_el if x.tag.split("}")[-1] == "is"), None)
                val = ""
                if c_type == "s" and v_el is not None and v_el.text:
                    s_idx = int(v_el.text)
                    val = shared[s_idx] if 0 <= s_idx < len(shared) else v_el.text
                elif c_type == "inlineStr" and is_el is not None:
                    val = "".join(is_el.itertext())
                elif v_el is not None and v_el.text:
                    val = v_el.text
                row_vals.append(val.strip().replace("|", "\\|"))
            if any(row_vals):
                rows_data.append(row_vals)
        if rows_data:
            cols = max(len(r) for r in rows_data)
            lines = [f"### 📊 {sname}"]
            for r_i, r in enumerate(rows_data[:300]):
                padded = r + [""] * (cols - len(r))
                lines.append("| " + " | ".join(padded) + " |")
                if r_i == 0:
                    lines.append("| " + " | ".join(["---"] * cols) + " |")
            out_sections.append("\n".join(lines))

    full = "\n\n".join(out_sections).strip()
    return full, max(1, len(sheet_files))


def read_rtf_bytes(data: bytes) -> str:
    """Reads Rich Text Format (.rtf) with full Turkish CP1254 & Unicode escape support."""
    raw = data.decode("latin1", errors="replace")
    out: list[str] = []
    skip_destinations = {
        "fonttbl", "colortbl", "stylesheet", "info", "pict", "object",
        "fldinst", "header", "footer", "xmlnstbl", "themedata", "colorschememapping"
    }
    stack: list[tuple[bool, int]] = []
    ignoring = False
    uc_skip = 1
    skip_chars = 0
    i = 0
    n = len(raw)

    while i < n:
        ch = raw[i]
        if ch == "{":
            stack.append((ignoring, uc_skip))
            if i + 2 < n and raw[i + 1] == "\\" and raw[i + 2] == "*":
                ignoring = True
                i += 3
                continue
            i += 1
        elif ch == "}":
            if stack:
                ignoring, uc_skip = stack.pop()
            i += 1
        elif ch == "\\":
            i += 1
            if i >= n:
                break
            nxt = raw[i]
            if nxt in ("\\", "{", "}"):
                if not ignoring and skip_chars == 0:
                    out.append(nxt)
                elif skip_chars > 0:
                    skip_chars -= 1
                i += 1
            elif nxt == "'":
                hex_code = raw[i + 1 : i + 3]
                i += 3
                if skip_chars > 0:
                    skip_chars -= 1
                elif not ignoring:
                    try:
                        b_val = int(hex_code, 16)
                        out.append(chr(WIN1254_TABLE.get(b_val, b_val)))
                    except ValueError:
                        pass
            elif nxt in ("\n", "\r"):
                if not ignoring:
                    out.append("\n")
                i += 1
            elif nxt == "~":
                if not ignoring and skip_chars == 0:
                    out.append(" ")
                i += 1
            else:
                m = re.match(r"([a-zA-Z]+)(-?\d*) ?", raw[i:])
                if not m:
                    i += 1
                    continue
                word, arg = m.group(1), m.group(2)
                i += m.end()
                if word in skip_destinations:
                    ignoring = True
                elif word == "uc" and arg:
                    uc_skip = max(0, int(arg))
                elif word == "u" and arg:
                    if not ignoring:
                        code = int(arg)
                        if code < 0:
                            code += 65536
                        out.append(chr(code))
                        skip_chars = uc_skip
                elif not ignoring:
                    if word in ("par", "pard", "line", "row", "sect"):
                        out.append("\n")
                    elif word in ("tab", "cell"):
                        out.append("\t")
        elif ch in ("\r", "\n"):
            i += 1
        else:
            if skip_chars > 0:
                skip_chars -= 1
            elif not ignoring:
                out.append(ch)
            i += 1

    text = "".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_doc_bytes(data: bytes, path: str | None = None) -> str:
    """Reads legacy Word 97-2003 (.doc) files via CFB piece table or LibreOffice fallback."""
    if data[:5] == b"{\\rtf":
        return read_rtf_bytes(data)

    # Try CFB piece table extraction (ported from evrak convert DocReader)
    if len(data) >= 512 and data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        try:
            text = _extract_cfb_word_text(data)
            if text and len(text.strip()) >= 10:
                return text.strip()
        except Exception:
            pass

    if path and shutil.which("soffice"):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run(
                    ["soffice", "--headless", "--convert-to", "txt:Text (encoded):UTF8", "--outdir", tmp, path],
                    capture_output=True, timeout=25, check=True,
                )
                txt_files = list(Path(tmp).glob("*.txt"))
                if txt_files:
                    return txt_files[0].read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            pass

    raise ValueError("Eski Word (.doc) dosyası okunamadı.")


def _extract_cfb_word_text(data: bytes) -> str | None:
    sector_shift = struct.unpack_from("<H", data, 0x1E)[0]
    sector_size = 1 << sector_shift
    dir_start = struct.unpack_from("<I", data, 0x30)[0]
    fat_sectors: list[int] = []
    for i in range(109):
        sec = struct.unpack_from("<I", data, 0x4C + i * 4)[0]
        if sec in (0xFFFFFFFE, 0xFFFFFFFF):
            break
        fat_sectors.append(sec)

    fat: list[int] = []
    per_sec = sector_size // 4
    for sec in fat_sectors:
        base = (sec + 1) * sector_size
        if base + sector_size > len(data):
            break
        fat.extend(struct.unpack_from(f"<{per_sec}I", data, base))

    def read_chain(start_sec: int, max_len: int) -> bytes:
        buf = bytearray()
        sec = start_sec
        seen: set[int] = set()
        while sec not in (0xFFFFFFFE, 0xFFFFFFFF) and sec < len(fat) and sec not in seen and len(buf) < max_len:
            seen.add(sec)
            base = (sec + 1) * sector_size
            buf.extend(data[base : base + sector_size])
            sec = fat[sec]
        return bytes(buf[:max_len])

    dir_bytes = read_chain(dir_start, len(data))
    streams: dict[str, bytes] = {}
    for at in range(0, len(dir_bytes) - 127, 128):
        name_len = struct.unpack_from("<H", dir_bytes, at + 0x40)[0]
        entry_type = dir_bytes[at + 0x42]
        if name_len < 2 or name_len > 64 or entry_type != 2:
            continue
        name = dir_bytes[at : at + name_len - 2].decode("utf-16-le", errors="replace")
        start = struct.unpack_from("<I", dir_bytes, at + 0x74)[0]
        size = struct.unpack_from("<I", dir_bytes, at + 0x78)[0]
        if size > 0 and start < len(fat):
            streams[name] = read_chain(start, size)

    word = streams.get("WordDocument")
    if not word or len(word) < 96:
        return None
    magic = struct.unpack_from("<H", word, 0)[0]
    if magic != 0xA5EC:
        return None
    fc_min = struct.unpack_from("<I", word, 0x18)[0]
    fc_mac = struct.unpack_from("<I", word, 0x1C)[0]
    if fc_mac <= fc_min or fc_mac > len(word):
        return None
    raw_slice = word[fc_min:fc_mac]
    # Clean control chars
    cleaned = decode_win1254(raw_slice)
    cleaned = cleaned.replace("\r", "\n").replace("\x07", "\t").replace("\x0b", "\n")
    cleaned = "".join(ch for ch in cleaned if ch >= " " or ch in ("\n", "\t"))
    return cleaned


def render_pdf_pages_as_images(path: str, max_pages: int = 5) -> list[str]:
    """Renders scanned PDF pages to PNG data URLs using Ghostscript (`gs`)."""
    if not shutil.which("gs"):
        return []
    urls: list[str] = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out_pattern = str(Path(tmp) / "page-%02d.png")
            subprocess.run(
                [
                    "gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m",
                    "-r150", f"-dFirstPage=1", f"-dLastPage={max_pages}",
                    f"-sOutputFile={out_pattern}", path,
                ],
                capture_output=True, timeout=30, check=True,
            )
            for png_file in sorted(Path(tmp).glob("page-*.png"))[:max_pages]:
                b64 = base64.b64encode(png_file.read_bytes()).decode("ascii")
                urls.append(f"data:image/png;base64,{b64}")
    except Exception:
        pass
    return urls


def extract_document_full(path: str, limit: int = 80000) -> ExtractedDocument:
    """Reads any supported document and returns structured metadata + clean text."""
    file = Path(path)
    if not file.exists():
        raise ValueError(f"Dosya bulunamadı: {file.name}")

    suffix = file.suffix.lower()
    data = file.read_bytes()
    signed = False
    signers: list[str] = []
    pages = 1
    rendered_pages: list[str] = []
    fmt_label = suffix.lstrip(".").upper() or "DOSYA"

    if suffix == ".udf":
        fmt_label = "UDF (UYAP)"
        content, signed, signers, pages = read_udf_bytes(data)
    elif suffix == ".pdf":
        fmt_label = "PDF"
        if shutil.which("pdfinfo"):
            try:
                info_out = subprocess.run(["pdfinfo", str(file)], capture_output=True, text=True, timeout=10).stdout
                m = re.search(r"Pages:\s+(\d+)", info_out)
                if m:
                    pages = int(m.group(1))
            except Exception:
                pass
        done = subprocess.run(["pdftotext", "-layout", str(file), "-"], capture_output=True, text=True, timeout=30, check=True)
        content = done.stdout.strip()
        if len(content) < 40 * max(1, min(pages, 3)):
            rendered_pages = render_pdf_pages_as_images(str(file), max_pages=4)
            if rendered_pages and not content:
                content = "[Taranmış PDF belgesi: Sayfalar görsel olarak eklendi ve OCR/Görsel modele hazırlandı.]"
    elif suffix == ".docx":
        fmt_label = "DOCX (Word)"
        content, pages = read_docx_bytes(data)
    elif suffix == ".doc":
        fmt_label = "DOC (Word 97-2003)"
        content = read_doc_bytes(data, str(file))
        pages = max(1, (len(content) + 2499) // 2500)
    elif suffix == ".rtf":
        fmt_label = "RTF"
        content = read_rtf_bytes(data)
        pages = max(1, (len(content) + 2499) // 2500)
    elif suffix == ".odt":
        fmt_label = "ODT"
        content, pages = read_odt_bytes(data)
    elif suffix == ".xlsx":
        fmt_label = "XLSX (Excel)"
        content, pages = read_xlsx_bytes(data)
    elif suffix == ".csv":
        fmt_label = "CSV"
        raw_csv = smart_decode(data)
        reader = list(csv.reader(io.StringIO(raw_csv)))
        if reader:
            cols = max(len(r) for r in reader)
            md_rows = []
            for idx, r in enumerate(reader[:300]):
                padded = [c.strip().replace("|", "\\|") for c in r] + [""] * (cols - len(r))
                md_rows.append("| " + " | ".join(padded) + " |")
                if idx == 0:
                    md_rows.append("| " + " | ".join(["---"] * cols) + " |")
            content = "\n".join(md_rows)
        else:
            content = raw_csv
    elif suffix in (".html", ".htm"):
        fmt_label = "HTML"
        raw_html = smart_decode(data)
        cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", raw_html)
        cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
        content = html.unescape(re.sub(r"\s+", " ", cleaned)).strip()
    elif suffix in SUPPORTED_DOC_EXTENSIONS or suffix in (".c", ".cpp", ".h", ".rs", ".go", ".java", ".sql", ".ini", ".toml", ".log"):
        content = smart_decode(data)
        pages = max(1, (len(content) + 2499) // 2500)
    else:
        raise ValueError(
            f"'{suffix}' biçimi desteklenmiyor. Desteklenenler: UDF (UYAP), PDF, DOCX, DOC, RTF, ODT, XLSX, CSV, TXT, MD."
        )

    if not content.strip() and not rendered_pages:
        raise ValueError("Belgeden metin okunamadı. Dosya boş veya taranmış görüntü olabilir.")

    total_chars = len(content)
    words = len(content.split())
    truncated = total_chars > limit
    if truncated:
        content = content[:limit] + f"\n\n[Belge {limit:,} karakter sınırında kısaltıldı.]"

    badge_parts = [fmt_label, f"{pages} sayfa" if pages > 1 else "1 sayfa", f"{words:,} kelime"]
    if signed:
        signer_str = ", ".join(signers[:2]) if signers else "E-İmzalı"
        badge_parts.append(f"✒️ {signer_str}")
    if rendered_pages:
        badge_parts.append("🔍 Görsel/OCR")

    return ExtractedDocument(
        path=str(file),
        name=file.name,
        format_label=fmt_label,
        text=content,
        page_count=pages,
        char_count=total_chars,
        word_count=words,
        signed=signed,
        signers=signers,
        rendered_pages=rendered_pages,
        summary_badge=" · ".join(badge_parts),
        truncated=truncated,
    )


# ============================================================================
# DOCUMENT CREATION & EXPORT (UDF, DOCX, PDF, RTF, MD, HTML, TXT)
# ============================================================================

def parse_markdown_to_blocks(markdown_text: str) -> list[DocBlock]:
    """Parses Markdown or structured text (headings, bold/italic, lists, tables) into DocBlocks."""
    lines = markdown_text.replace("\r\n", "\n").split("\n")
    blocks: list[DocBlock] = []
    i = 0
    n = len(lines)

    def parse_inline_spans(raw_line: str) -> tuple[str, list[DocSpan]]:
        plain_chars: list[str] = []
        spans: list[DocSpan] = []
        token_re = re.compile(r"(\*\*\*.*?\*\*\*|\*\*.*?\*\*|\*[^*\n]+\*|`[^`\n]+`)")
        pos = 0
        for m in token_re.finditer(raw_line):
            if m.start() > pos:
                seg = raw_line[pos : m.start()]
                start_off = sum(len(x) for x in plain_chars)
                plain_chars.append(seg)
                spans.append(DocSpan(start=start_off, length=len(seg)))
            tok = m.group(0)
            start_off = sum(len(x) for x in plain_chars)
            if tok.startswith("***") and tok.endswith("***"):
                inner = tok[3:-3]
                plain_chars.append(inner)
                spans.append(DocSpan(start=start_off, length=len(inner), bold=True, italic=True))
            elif tok.startswith("**") and tok.endswith("**"):
                inner = tok[2:-2]
                plain_chars.append(inner)
                spans.append(DocSpan(start=start_off, length=len(inner), bold=True))
            elif tok.startswith("*") and tok.endswith("*"):
                inner = tok[1:-1]
                plain_chars.append(inner)
                spans.append(DocSpan(start=start_off, length=len(inner), italic=True))
            elif tok.startswith("`") and tok.endswith("`"):
                inner = tok[1:-1]
                plain_chars.append(inner)
                spans.append(DocSpan(start=start_off, length=len(inner), font_family="Courier New"))
            pos = m.end()
        if pos < len(raw_line):
            seg = raw_line[pos:]
            start_off = sum(len(x) for x in plain_chars)
            plain_chars.append(seg)
            spans.append(DocSpan(start=start_off, length=len(seg)))
        plain = "".join(plain_chars)
        if not spans:
            spans.append(DocSpan(start=0, length=len(plain)))
        return plain, spans

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Table detection
        if stripped.startswith("|") and stripped.endswith("|") and "|" in stripped[1:-1]:
            table_rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                row_str = lines[i].strip()[1:-1]
                cells = [c.strip() for c in row_str.split("|")]
                if not all(re.match(r"^:?-{2,}:?$", c) for c in cells if c):
                    table_rows.append(cells)
                i += 1
            if table_rows:
                blocks.append(DocBlock(kind="table", rows=table_rows))
            continue

        # Code block detection
        if stripped.startswith("```"):
            i += 1
            code_lines: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1
            code_txt = "\n".join(code_lines)
            blocks.append(DocBlock(
                kind="code",
                text=code_txt,
                spans=[DocSpan(start=0, length=len(code_txt), font_family="Courier New", font_size=10.5)],
            ))
            continue

        # Heading detection
        hm = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if hm:
            lvl = len(hm.group(1))
            plain, _ = parse_inline_spans(hm.group(2).strip())
            h_size = {1: 15.0, 2: 13.5, 3: 12.5, 4: 12.0}.get(lvl, 12.0)
            align = 1 if lvl == 1 and plain.isupper() else 0
            blocks.append(DocBlock(
                kind="heading",
                level=lvl,
                text=plain,
                alignment=align,
                spans=[DocSpan(start=0, length=len(plain), bold=True, font_size=h_size)],
            ))
            i += 1
            continue

        # List detection
        bm = re.match(r"^([-*•])\s+(.*)$", stripped)
        om = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if bm or om:
            raw_item = bm.group(2) if bm else om.group(2)
            plain, spans = parse_inline_spans(raw_item)
            blocks.append(DocBlock(
                kind="list",
                ordered=bool(om),
                text=plain,
                spans=spans,
            ))
            i += 1
            continue

        # All-caps legal header centering (e.g., "ANKARA NÖBETÇİ ASLİYE HUKUK MAHKEMESİNE")
        plain, spans = parse_inline_spans(line)
        align = 0
        if stripped and len(stripped) < 90 and stripped == stripped.upper() and any(
            k in stripped for k in ("MAHKEMESİ", "MAHKEMESI", "DAİRESİ", "MÜDÜRLÜĞÜ", "BAŞKANLIĞI", "CUMHURİYET", "DİLEKÇE", "SÖZLEŞME", "TUTANAK")
        ):
            align = 1
            for sp in spans:
                sp.bold = True

        blocks.append(DocBlock(kind="paragraph", text=plain, alignment=align, spans=spans))
        i += 1

    return blocks or [DocBlock(kind="paragraph", text="", spans=[DocSpan(start=0, length=0)])]


def write_udf_bytes(markdown_text: str) -> bytes:
    """Creates a genuine UYAP-compatible .udf archive (content.xml inside ZIP)."""
    blocks = parse_markdown_to_blocks(markdown_text)
    full_text_parts: list[str] = []
    elements_xml: list[str] = []
    offset = 0

    def xml_attr(val: object) -> str:
        return html.escape(str(val), quote=True)

    for b in blocks:
        if b.kind == "table" and b.rows:
            col_count = max(len(r) for r in b.rows)
            row_xmls: list[str] = []
            for r_idx, row in enumerate(b.rows):
                padded = row + [""] * (col_count - len(row))
                cell_xmls: list[str] = []
                for cell_txt in padded:
                    c_start = offset
                    full_text_parts.append(cell_txt + "\n")
                    offset += len(cell_txt) + 1
                    bold_attr = "true" if r_idx == 0 else "false"
                    cell_xmls.append(
                        f'<cell cellSpan="1" rowSpan="1">'
                        f'<paragraph resolver="hvl-default" Alignment="0">'
                        f'<content bold="{bold_attr}" italic="false" underline="false" '
                        f'strikethrough="false" superscript="false" subscript="false" '
                        f'family="Times New Roman" size="11.0" startOffset="{c_start}" length="{len(cell_txt)}"/>'
                        f'<content family="Times New Roman" size="11.0" startOffset="{c_start + len(cell_txt)}" length="1"/>'
                        f'</paragraph></cell>'
                    )
                rtype = "headerRow" if r_idx == 0 else "dataRow"
                row_xmls.append(f'<row rowType="{rtype}">{"".join(cell_xmls)}</row>')
            elements_xml.append(f'<table border="borderCell">{"".join(row_xmls)}</table>')
            continue

        p_start = offset
        full_text_parts.append(b.text + "\n")
        offset += len(b.text) + 1

        attrs = ['resolver="hvl-default"', f'Alignment="{b.alignment}"']
        if b.kind == "list":
            attrs.append('Numbered="true"' if b.ordered else 'Bulleted="true"')

        runs_xml: list[str] = []
        if b.spans and b.text:
            for sp in b.spans:
                runs_xml.append(
                    f'<content bold="{"true" if sp.bold else "false"}" '
                    f'italic="{"true" if sp.italic else "false"}" '
                    f'underline="{"true" if sp.underline else "false"}" '
                    f'strikethrough="{"true" if sp.strikethrough else "false"}" '
                    f'superscript="false" subscript="false" '
                    f'family="{xml_attr(sp.font_family)}" size="{sp.font_size}" '
                    f'startOffset="{p_start + sp.start}" length="{sp.length}"/>'
                )
        runs_xml.append(
            f'<content family="Times New Roman" size="12.0" startOffset="{p_start + len(b.text)}" length="1"/>'
        )
        elements_xml.append(f'<paragraph {" ".join(attrs)}>{"".join(runs_xml)}</paragraph>')

    raw_content = "".join(full_text_parts)
    cdata_parts = raw_content.split("]]>")
    cdata_xml = "".join(
        f"<![CDATA[{'>' if idx > 0 else ''}{part}{']]' if idx < len(cdata_parts) - 1 else ''}]]>"
        for idx, part in enumerate(cdata_parts)
    )

    xml_doc = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<template format_id="1.8">'
        f"<content>{cdata_xml}</content>"
        "<properties>"
        '<pageFormat mediaSizeName="1" paperOrientation="1" '
        'leftMargin="42.525" rightMargin="42.525" topMargin="42.525" bottomMargin="42.525" '
        'headerFOffset="20.0" footerFOffset="20.0"/>'
        "</properties>"
        f'<elements resolver="hvl-default">{"".join(elements_xml)}</elements>'
        "<styles>"
        '<style name="hvl-default" family="Times New Roman" size="12.0" bold="false" italic="false"/>'
        "</styles>"
        "</template>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.xml", xml_doc.encode("utf-8"))
    return buf.getvalue()


def write_docx_bytes(markdown_text: str) -> bytes:
    """Creates a clean, standards-compliant Word (.docx) file from Markdown/text."""
    blocks = parse_markdown_to_blocks(markdown_text)
    W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    body_parts: list[str] = []
    for b in blocks:
        if b.kind == "table" and b.rows:
            tr_xmls: list[str] = []
            cols = max(len(r) for r in b.rows)
            for r_idx, row in enumerate(b.rows):
                padded = row + [""] * (cols - len(row))
                tc_xmls: list[str] = []
                for c_txt in padded:
                    b_tag = "<w:b/>" if r_idx == 0 else ""
                    tc_xmls.append(
                        f"<w:tc><w:tcPr><w:tcBorders>"
                        f'<w:top w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
                        f'<w:bottom w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
                        f'<w:left w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
                        f'<w:right w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
                        f"</w:tcBorders></w:tcPr>"
                        f"<w:p><w:r><w:rPr>{b_tag}</w:rPr>"
                        f'<w:t xml:space="preserve">{html.escape(c_txt)}</w:t>'
                        f"</w:r></w:p></w:tc>"
                    )
                tr_xmls.append(f"<w:tr>{''.join(tc_xmls)}</w:tr>")
            body_parts.append(f"<w:tbl><w:tblPr><w:tblW w:w=\"5000\" w:type=\"pct\"/></w:tblPr>{''.join(tr_xmls)}</w:tbl>")
            continue

        jc = {0: "left", 1: "center", 2: "right", 3: "both"}.get(b.alignment, "left")
        p_pr = f'<w:pPr><w:jc w:val="{jc}"/></w:pPr>'
        runs: list[str] = []
        if b.kind == "list":
            prefix = "• " if not b.ordered else "1. "
            runs.append(f'<w:r><w:t xml:space="preserve">{prefix}</w:t></w:r>')
        for sp in b.spans:
            seg = b.text[sp.start : sp.start + sp.length]
            if not seg:
                continue
            rpr = []
            if sp.bold:
                rpr.append("<w:b/>")
            if sp.italic:
                rpr.append("<w:i/>")
            if sp.underline:
                rpr.append('<w:u w:val="single"/>')
            sz = int(round(sp.font_size * 2))
            rpr.append(f'<w:sz w:val="{sz}"/>')
            rpr.append(f'<w:rFonts w:ascii="{html.escape(sp.font_family)}" w:hAnsi="{html.escape(sp.font_family)}"/>')
            runs.append(
                f"<w:r><w:rPr>{''.join(rpr)}</w:rPr>"
                f'<w:t xml:space="preserve">{html.escape(seg)}</w:t></w:r>'
            )
        body_parts.append(f"<w:p>{p_pr}{''.join(runs)}</w:p>")

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}">'
        f"<w:body>{''.join(body_parts)}"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
        "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types.encode("utf-8"))
        zf.writestr("_rels/.rels", rels.encode("utf-8"))
        zf.writestr("word/document.xml", document_xml.encode("utf-8"))
    return buf.getvalue()


def write_rtf_bytes(markdown_text: str) -> bytes:
    """Creates a Turkish-safe RTF document using \\uNNNN? Unicode escapes."""
    blocks = parse_markdown_to_blocks(markdown_text)

    def rtf_escape(s: str) -> str:
        out: list[str] = []
        for ch in s:
            code = ord(ch)
            if ch in ("\\", "{", "}"):
                out.append("\\" + ch)
            elif ch == "\n":
                out.append("\\line ")
            elif ch == "\t":
                out.append("\\tab ")
            elif 32 <= code <= 126:
                out.append(ch)
            else:
                signed = code - 65536 if code > 32767 else code
                out.append(f"\\u{signed}?")
        return "".join(out)

    body: list[str] = [
        "{\\rtf1\\ansi\\ansicpg1254\\uc1\\deff0"
        "{\\fonttbl{\\f0\\froman\\fcharset162 Times New Roman;}{\\f1\\fmodern\\fcharset162 Courier New;}}"
        "\\paperw11906\\paperh16838\\margl1440\\margr1440\\margt1440\\margb1440\n"
    ]

    for b in blocks:
        if b.kind == "table" and b.rows:
            cols = max(len(r) for r in b.rows)
            col_w = 9000 // max(1, cols)
            for row in b.rows:
                body.append("\\trowd")
                for c_idx in range(cols):
                    body.append(f"\\cellx{(c_idx + 1) * col_w}")
                padded = row + [""] * (cols - len(row))
                for cell_txt in padded:
                    body.append(f"\\pard\\intbl\\f0\\fs22 {rtf_escape(cell_txt)}\\cell")
                body.append("\\row\n")
            continue

        align_cmd = {0: "\\ql", 1: "\\qc", 2: "\\qr", 3: "\\qj"}.get(b.alignment, "\\ql")
        body.append(f"\\pard{align_cmd}\\sa120 ")
        if b.kind == "list":
            body.append(rtf_escape("• " if not b.ordered else "1. "))
        for sp in b.spans:
            seg = b.text[sp.start : sp.start + sp.length]
            if not seg:
                continue
            cmds = [f"\\f{1 if 'Courier' in sp.font_family else 0}", f"\\fs{int(round(sp.font_size * 2))}"]
            if sp.bold:
                cmds.append("\\b")
            if sp.italic:
                cmds.append("\\i")
            if sp.underline:
                cmds.append("\\ul")
            body.append("{" + "".join(cmds) + " " + rtf_escape(seg) + "}")
        body.append("\\par\n")

    body.append("}")
    return "".join(body).encode("ascii", errors="replace")


def markdown_to_styled_html(markdown_text: str, title: str = "LifeOS Belgesi") -> str:
    """Converts Markdown into a rich, print-ready HTML document."""
    blocks = parse_markdown_to_blocks(markdown_text)
    html_parts: list[str] = []

    for b in blocks:
        if b.kind == "table" and b.rows:
            rows_html: list[str] = []
            for r_idx, row in enumerate(b.rows):
                tag = "th" if r_idx == 0 else "td"
                bg = ' style="background:#f3f0fa;font-weight:bold;padding:6px 10px;border:1px solid #ccc;"' if r_idx == 0 else ' style="padding:6px 10px;border:1px solid #ccc;"'
                cells = "".join(f"<{tag}{bg}>{html.escape(c)}</{tag}>" for c in row)
                rows_html.append(f"<tr>{cells}</tr>")
            html_parts.append(
                f'<table style="border-collapse:collapse;width:100%;margin:10px 0;font-size:11pt;">{"".join(rows_html)}</table>'
            )
            continue

        if b.kind == "code":
            html_parts.append(
                f'<pre style="background:#f5f4f8;padding:10px;border-radius:6px;font-family:monospace;font-size:10pt;">{html.escape(b.text)}</pre>'
            )
            continue

        align_css = {0: "left", 1: "center", 2: "right", 3: "justify"}.get(b.alignment, "left")
        span_html: list[str] = []
        if b.kind == "list":
            span_html.append("• " if not b.ordered else "1. ")
        for sp in b.spans:
            seg = html.escape(b.text[sp.start : sp.start + sp.length])
            if sp.bold:
                seg = f"<b>{seg}</b>"
            if sp.italic:
                seg = f"<i>{seg}</i>"
            if sp.underline:
                seg = f"<u>{seg}</u>"
            span_html.append(seg)
        inner = "".join(span_html) or "&nbsp;"
        if b.kind == "heading":
            lvl = min(4, max(1, b.level))
            html_parts.append(f'<h{lvl} style="text-align:{align_css};margin:12px 0 6px 0;">{inner}</h{lvl}>')
        else:
            html_parts.append(f'<p style="text-align:{align_css};margin:4px 0;line-height:1.45;">{inner}</p>')

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body { font-family: 'Times New Roman', 'Noto Serif', serif; font-size: 12pt; color: #111; }</style>"
        f"</head><body>{''.join(html_parts)}</body></html>"
    )


def write_pdf_bytes(markdown_text: str, title: str = "LifeOS Belgesi") -> bytes:
    """Renders Markdown/text to a clean A4 PDF using PySide6 QTextDocument + QPdfWriter."""
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QMarginsF
    from PySide6.QtGui import QGuiApplication, QPageLayout, QPageSize, QPdfWriter, QTextDocument

    # Ensure a QGuiApplication instance exists when called from CLI/MCP
    _app = QGuiApplication.instance()
    created_app = None
    if _app is None:
        created_app = QGuiApplication(["lifeos-pdf", "-platform", "offscreen"])

    try:
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)

        writer = QPdfWriter(buffer)
        writer.setTitle(title)
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageMargins(QMarginsF(18, 18, 18, 18), QPageLayout.Unit.Millimeter)

        doc = QTextDocument()
        doc.setHtml(markdown_to_styled_html(markdown_text, title=title))
        doc.print_(writer)
        buffer.close()
        return bytes(byte_array)
    finally:
        if created_app is not None:
            pass


def export_document(content: str, target_format: str, output_path: str, title: str = "LifeOS Belgesi") -> str:
    """Exports text/Markdown content to .udf, .docx, .pdf, .rtf, .md, .html, or .txt."""
    fmt = target_format.lower().lstrip(".")
    out = Path(output_path)
    if out.suffix.lower() != f".{fmt}":
        out = out.with_suffix(f".{fmt}")
    out.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "udf":
        out.write_bytes(write_udf_bytes(content))
    elif fmt == "docx":
        out.write_bytes(write_docx_bytes(content))
    elif fmt == "rtf":
        out.write_bytes(write_rtf_bytes(content))
    elif fmt == "pdf":
        out.write_bytes(write_pdf_bytes(content, title=title))
    elif fmt in ("html", "htm"):
        out.write_text(markdown_to_styled_html(content, title=title), encoding="utf-8")
    elif fmt in ("md", "txt"):
        out.write_text(content, encoding="utf-8")
    else:
        raise ValueError(f"Desteklenmeyen dışa aktarma biçimi: {fmt}. Desteklenenler: udf, docx, pdf, rtf, md, txt, html.")
    return str(out)


def convert_document_file(source_path: str, target_format: str, output_path: str | None = None) -> dict:
    """Reads any supported document (.udf, .docx, .doc, .rtf, .odt, .pdf, .xlsx, .txt) and converts it to target_format."""
    extracted = extract_document_full(source_path, limit=500000)
    src = Path(source_path)
    fmt = target_format.lower().lstrip(".")
    if not output_path:
        output_path = str(src.with_name(f"{src.stem}_donusturuldu.{fmt}"))
    saved = export_document(extracted.text, fmt, output_path, title=src.stem)
    return {
        "source": str(src),
        "source_format": extracted.format_label,
        "target_format": fmt.upper(),
        "output_path": saved,
        "pages": extracted.page_count,
        "signed": extracted.signed,
        "signers": extracted.signers,
    }

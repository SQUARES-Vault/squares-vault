"""Zero-dependency text extractors for office and tabular documents (.docx, .xlsx, .csv, .txt)."""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Optional

from squares_vault.logging_setup import get_logger

log = get_logger(__name__)

# XML Namespaces used in OpenXML standards
WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
EXCEL_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

SUPPORTED_OFFICE_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/csv",
    "text/tab-separated-values",
    "text/markdown",
}

SUPPORTED_OFFICE_EXTS = {
    ".docx",
    ".xlsx",
    ".csv",
    ".txt",
    ".tsv",
    ".md",
}


def is_supported_office_doc(filename: str = "", mime: str = "") -> bool:
    """Check if file can be parsed by the document extraction engine."""
    fn_lower = (filename or "").lower()
    m_lower = (mime or "").lower()

    if m_lower in ("application/pdf", "image/jpeg", "image/png", "image/webp"):
        return True

    if any(m_lower.startswith(p) for p in ("text/", "image/")):
        return True

    if m_lower in SUPPORTED_OFFICE_MIMES:
        return True

    if any(fn_lower.endswith(ext) for ext in SUPPORTED_OFFICE_EXTS):
        return True

    if fn_lower.endswith((".pdf", ".jpg", ".jpeg", ".png", ".webp")):
        return True

    return False


def extract_text_from_plain(file_bytes: bytes) -> str:
    """Decode text bytes using UTF-8 or Latin-1."""
    try:
        return file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return file_bytes.decode("latin-1", errors="replace")


def extract_text_from_docx(file_bytes: bytes) -> Optional[str]:
    """Extract paragraphs and tables from a .docx file using standard library zipfile and XML parser."""
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            if "word/document.xml" not in z.namelist():
                return None
            xml_bytes = z.read("word/document.xml")
            root = ET.fromstring(xml_bytes)

            lines = []
            # Extract paragraphs and tables in sequence
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag == "p":
                    # Collect all text inside this paragraph
                    texts = [t.text for t in elem.iter() if t.text and t.tag.endswith("t")]
                    p_text = "".join(texts).strip()
                    if p_text:
                        lines.append(p_text)
                elif tag == "tr":
                    # Row in table
                    cells = []
                    for tc in elem.findall(".//w:tc", WORD_NS):
                        cell_texts = [t.text for t in tc.iter() if t.text and t.tag.endswith("t")]
                        cell_str = "".join(cell_texts).strip()
                        if cell_str:
                            cells.append(cell_str)
                    if cells:
                        lines.append(" | ".join(cells))

            # Deduplicate consecutive identical lines from iteration
            deduped = []
            for ln in lines:
                if not deduped or deduped[-1] != ln:
                    deduped.append(ln)

            return "\n".join(deduped).strip() or None
    except Exception as e:
        log.warning("docx_extraction_failed", error=str(e))
        return None


def extract_text_from_xlsx(file_bytes: bytes) -> Optional[str]:
    """Extract spreadsheet text and cell rows from .xlsx file."""
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            names = z.namelist()
            # 1. Parse shared strings table if present
            shared_strings = []
            if "xl/sharedStrings.xml" in names:
                sst_root = ET.fromstring(z.read("xl/sharedStrings.xml"))
                for si in sst_root.iter():
                    if si.tag.endswith("si"):
                        t_parts = [t.text for t in si.iter() if t.text and t.tag.endswith("t")]
                        shared_strings.append("".join(t_parts))

            # 2. Parse worksheets
            sheet_files = [n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
            if not sheet_files:
                return None

            all_lines = []
            for sf in sheet_files:
                sheet_root = ET.fromstring(z.read(sf))
                for row in sheet_root.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
                    row_vals = []
                    for c in row.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
                        v = c.find(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
                        if v is not None and v.text:
                            raw_val = v.text
                            cell_type = c.attrib.get("t")
                            if cell_type == "s" and raw_val.isdigit():
                                idx = int(raw_val)
                                if 0 <= idx < len(shared_strings):
                                    row_vals.append(shared_strings[idx])
                                else:
                                    row_vals.append(raw_val)
                            else:
                                row_vals.append(raw_val)
                    if row_vals:
                        all_lines.append(" | ".join(row_vals))

            return "\n".join(all_lines).strip() or None
    except Exception as e:
        log.warning("xlsx_extraction_failed", error=str(e))
        return None


def extract_text_from_document(
    file_bytes: bytes, filename: str = "", mime: str = ""
) -> Optional[str]:
    """Dispatcher to extract textual content from any supported office/text document."""
    fn = (filename or "").lower()
    m = (mime or "").lower()

    if fn.endswith(".docx") or "wordprocessingml" in m:
        return extract_text_from_docx(file_bytes)
    if fn.endswith(".xlsx") or "spreadsheetml" in m:
        return extract_text_from_xlsx(file_bytes)
    if fn.endswith((".csv", ".tsv", ".txt", ".md")) or m.startswith("text/"):
        return extract_text_from_plain(file_bytes)

    # Magic byte check for docx/xlsx (PK zip)
    if file_bytes[:4] == b"PK\x03\x04":
        # Try docx first, then xlsx
        docx_res = extract_text_from_docx(file_bytes)
        if docx_res:
            return docx_res
        xlsx_res = extract_text_from_xlsx(file_bytes)
        if xlsx_res:
            return xlsx_res

    return None

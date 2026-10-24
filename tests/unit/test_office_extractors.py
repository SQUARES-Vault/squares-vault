import io
import zipfile
import pytest

from squares_vault.ops.office import (
    extract_text_from_docx,
    extract_text_from_xlsx,
    extract_text_from_plain,
    extract_text_from_document,
    is_supported_office_doc,
)

def test_extract_text_from_plain():
    content = "Invoice No: 1234\nDate: 2026-10-24\nTotal: 450.00"
    raw_bytes = content.encode("utf-8")
    extracted = extract_text_from_plain(raw_bytes)
    assert "Invoice No: 1234" in extracted
    assert "450.00" in extracted

def test_extract_text_from_csv():
    content = "Item,Qty,Price\nWidget A,2,100\nService B,1,250"
    raw_bytes = content.encode("utf-8")
    extracted = extract_text_from_plain(raw_bytes)
    assert "Widget A" in extracted
    assert "250" in extracted

def test_extract_text_from_docx():
    # Build a minimal valid docx in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # word/document.xml with namespaces
        doc_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>'
            '<w:p><w:r><w:t>Rental Agreement Contract</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Tenant: John Doe</w:t></w:r></w:p>'
            '<w:tbl>'
            '<w:tr><w:tc><w:p><w:r><w:t>Monthly Rent</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>₹25,000</w:t></w:r></w:p></w:tc></w:tr>'
            '</w:tbl>'
            '</w:body>'
            '</w:document>'
        )
        z.writestr("word/document.xml", doc_xml)

    docx_bytes = buf.getvalue()
    extracted = extract_text_from_docx(docx_bytes)
    assert extracted is not None
    assert "Rental Agreement Contract" in extracted
    assert "Tenant: John Doe" in extracted
    assert "Monthly Rent" in extracted
    assert "₹25,000" in extracted

def test_extract_text_from_xlsx():
    # Build a minimal valid xlsx in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        shared_strings = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<si><t>Electricity Bill</t></si>'
            '<si><t>Amount Due</t></si>'
            '</sst>'
        )
        z.writestr("xl/sharedStrings.xml", shared_strings)
        
        sheet1 = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>2026-10-20</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>1850.50</v></c></row>'
            '</sheetData>'
            '</worksheet>'
        )
        z.writestr("xl/worksheets/sheet1.xml", sheet1)

    xlsx_bytes = buf.getvalue()
    extracted = extract_text_from_xlsx(xlsx_bytes)
    assert extracted is not None
    assert "Electricity Bill" in extracted
    assert "1850.50" in extracted

def test_extract_text_from_document_dispatcher():
    # txt
    assert "Hello" in extract_text_from_document(b"Hello world", filename="notes.txt")
    # csv
    assert "A,B" in extract_text_from_document(b"A,B\n1,2", filename="data.csv")
    # unsupported binary returns None
    assert extract_text_from_document(b"\x00\x01\x02\x03\x04", filename="archive.zip") is None

def test_is_supported_office_doc():
    assert is_supported_office_doc("doc.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document") is True
    assert is_supported_office_doc("sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") is True
    assert is_supported_office_doc("bill.csv", "text/csv") is True
    assert is_supported_office_doc("note.txt", "text/plain") is True
    assert is_supported_office_doc("file.pdf", "application/pdf") is True
    assert is_supported_office_doc("photo.png", "image/png") is True
    assert is_supported_office_doc("other.exe", "application/x-msdownload") is False

from io import BytesIO

import pytest
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from cap_931.document_parser import (
    MAX_UPLOAD_BYTES,
    DocumentError,
    parse_product_document,
)
from cap_931.schemas import MAX_PRODUCT_DOCUMENT_CHARS


def text_pdf(text: str) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, text)
    pdf.save()
    return buffer.getvalue()


def blank_pdf(*, encrypted: bool = False) -> bytes:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    if encrypted:
        writer.encrypt("secret")
    writer.write(buffer)
    return buffer.getvalue()


def test_valid_txt_document():
    document = parse_product_document(
        "overview.TXT",
        b"Product overview\n\nFaster analytics for sales teams.",
    )

    assert document.file_type == "txt"
    assert document.content == "Product overview\nFaster analytics for sales teams."
    assert not document.truncated


def test_valid_pdf_document():
    document = parse_product_document(
        "overview.pdf", text_pdf("Faster analytics for sales teams.")
    )

    assert document.file_type == "pdf"
    assert "Faster analytics for sales teams." in document.content
    assert not document.truncated


@pytest.mark.parametrize(
    ("filename", "data"),
    [("empty.txt", b""), ("whitespace.txt", b" \n\t")],
)
def test_empty_documents_are_rejected(filename, data):
    with pytest.raises(DocumentError, match="empty|readable text"):
        parse_product_document(filename, data)


def test_oversized_upload_is_rejected():
    with pytest.raises(DocumentError, match="5 MB"):
        parse_product_document("large.txt", b"x" * (MAX_UPLOAD_BYTES + 1))


@pytest.mark.parametrize(
    ("filename", "data", "message"),
    [
        ("broken.pdf", b"%PDF-1.7\nnot a real PDF", "malformed or unreadable"),
        ("renamed.pdf", b"This is plain text.", "valid PDF signature"),
        ("renamed.txt", b"%PDF-1.7\n", "PDF data"),
        ("binary.txt", b"product\x00overview", "binary data"),
    ],
)
def test_malformed_or_mismatched_documents_are_rejected(filename, data, message):
    with pytest.raises(DocumentError, match=message):
        parse_product_document(filename, data)


def test_encrypted_pdf_is_rejected():
    with pytest.raises(DocumentError, match="Encrypted PDF"):
        parse_product_document("encrypted.pdf", blank_pdf(encrypted=True))


def test_pdf_without_extractable_text_explains_ocr_limit():
    with pytest.raises(DocumentError, match="require OCR"):
        parse_product_document("scan.pdf", blank_pdf())


def test_document_text_is_bounded_for_model_context():
    document = parse_product_document(
        "long.txt", b"x" * (MAX_PRODUCT_DOCUMENT_CHARS + 100)
    )

    assert len(document.content) == MAX_PRODUCT_DOCUMENT_CHARS
    assert document.truncated

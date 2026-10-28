"""Safe, bounded text extraction for optional product overview documents."""

from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .schemas import MAX_PRODUCT_DOCUMENT_CHARS, ProductDocument

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".pdf", ".txt"}


class DocumentError(ValueError):
    """Raised when an uploaded product document cannot be safely used."""


def _safe_filename(filename: str) -> str:
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or len(name) > 255:
        raise DocumentError("The product document filename is missing or too long.")
    return name


def _normalize_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _bound_text(text: str) -> tuple[str, bool]:
    normalized = _normalize_text(text)
    if not normalized:
        raise DocumentError("The product document does not contain readable text.")
    truncated = len(normalized) > MAX_PRODUCT_DOCUMENT_CHARS
    return normalized[:MAX_PRODUCT_DOCUMENT_CHARS], truncated


def _extract_txt(data: bytes) -> tuple[str, bool]:
    if data.startswith(b"%PDF-"):
        raise DocumentError("The file contents are PDF data, but the filename ends in .txt.")
    if b"\x00" in data:
        raise DocumentError("The TXT upload appears to contain binary data.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentError("TXT product documents must use UTF-8 text encoding.") from exc

    disallowed_controls = sum(
        1 for character in text if ord(character) < 32 and character not in "\n\r\t\f"
    )
    if disallowed_controls > max(1, len(text) // 100):
        raise DocumentError("The TXT upload appears to contain binary data.")
    return _bound_text(text)


def _extract_pdf(data: bytes) -> tuple[str, bool]:
    if b"%PDF-" not in data[:1024]:
        raise DocumentError("The file does not have a valid PDF signature.")
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise DocumentError("Encrypted PDF product documents are not supported.")

        page_text = []
        extracted_chars = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            page_text.append(text)
            extracted_chars += len(text)
            if extracted_chars > MAX_PRODUCT_DOCUMENT_CHARS:
                break
    except DocumentError:
        raise
    except (PdfReadError, OSError, TypeError, ValueError) as exc:
        raise DocumentError("The PDF is malformed or unreadable.") from exc
    except Exception as exc:
        raise DocumentError("The PDF text could not be extracted.") from exc

    combined = _normalize_text("\n".join(page_text))
    if not combined:
        raise DocumentError(
            "No extractable text was found in the PDF. Scanned PDFs require OCR, "
            "which is outside the current project scope."
        )
    return combined[:MAX_PRODUCT_DOCUMENT_CHARS], (
        extracted_chars > MAX_PRODUCT_DOCUMENT_CHARS
        or len(combined) > MAX_PRODUCT_DOCUMENT_CHARS
    )


def parse_product_document(filename: str, data: bytes) -> ProductDocument:
    """Validate an uploaded PDF or TXT file and return bounded extracted text."""
    name = _safe_filename(filename)
    extension = Path(name).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise DocumentError("Product documents must use a .pdf or .txt extension.")
    if not isinstance(data, bytes):
        raise DocumentError("The product document could not be read as bytes.")
    if not data:
        raise DocumentError("The product document is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError("The product document exceeds the 5 MB upload limit.")

    if extension == ".pdf":
        content, truncated = _extract_pdf(data)
    else:
        content, truncated = _extract_txt(data)

    return ProductDocument(
        filename=name,
        file_type=extension.removeprefix("."),
        content=content,
        truncated=truncated,
    )

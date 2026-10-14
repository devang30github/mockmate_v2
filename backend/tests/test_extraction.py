import io
import zipfile

import pytest

from app.services import extraction
from app.services.documents import safe_filename
from app.services.extraction import (
    DocumentError,
    UnreadableDocument,
    UnsupportedFileType,
    extract_text,
    normalize_text,
)
from tests.factories import make_blank_pdf, make_docx, make_pdf


def test_pdf_text_is_extracted():
    result = extract_text("resume.pdf", make_pdf())
    assert result.page_count == 1
    assert "FastAPI" in result.text and "pgvector" in result.text


def test_docx_text_bullets_and_tables_are_extracted():
    data = make_docx(bullets=["Shipped a recommender"], table_rows=[("Python", "5 years")])
    result = extract_text("resume.docx", data)
    assert "FastAPI" in result.text
    assert "• Shipped a recommender" in result.text
    assert "Python | 5 years" in result.text
    assert result.page_count is None


def test_blank_pdf_is_reported_as_scanned():
    with pytest.raises(UnreadableDocument, match="scanned"):
        extract_text("scan.pdf", make_blank_pdf())


def test_garbage_bytes_named_pdf_rejected():
    with pytest.raises(UnreadableDocument):
        extract_text("resume.pdf", b"this is not a pdf at all")


def test_docx_renamed_as_pdf_rejected():
    with pytest.raises(UnreadableDocument):
        extract_text("resume.pdf", make_docx())


def test_unsupported_extension_rejected():
    with pytest.raises(UnsupportedFileType):
        extract_text("resume.txt", b"hello")


def test_pdf_page_limit_enforced():
    with pytest.raises(UnreadableDocument, match="pages"):
        extract_text("resume.pdf", make_pdf(pages=3), max_pages=2)


def test_docx_zip_bomb_rejected(monkeypatch):
    monkeypatch.setattr(extraction, "MAX_DOCX_UNCOMPRESSED", 1000)
    with pytest.raises(DocumentError, match="unsafe size"):
        extract_text("resume.docx", make_docx())


def test_zip_without_word_document_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(UnreadableDocument):
        extract_text("resume.docx", buf.getvalue())


def test_normalize_expands_ligatures_and_collapses_blank_lines():
    assert normalize_text("\ufb01nal\n\n\n\n\nround") == "final\n\nround"


def test_normalize_preserves_technical_terms():
    text = "C++, Node.js, k8s, CI/CD, scikit-learn, .NET"
    assert normalize_text(text) == text


def test_safe_filename_strips_paths_and_control_chars():
    assert safe_filename("..\\..\\evil\x00/resume.pdf") == "resume.pdf"
    assert safe_filename(None) == "resume"
    assert len(safe_filename("a" * 400 + ".pdf")) == 255
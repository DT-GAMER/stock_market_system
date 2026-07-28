from pathlib import Path

from pypdf import PdfReader


class PdfExtractionError(RuntimeError):
    pass


def extract_pdf_text(path: str) -> tuple[str, int, list[str]]:
    file_path = Path(path)
    if not file_path.exists():
        raise PdfExtractionError("uploaded report file is missing from local storage")
    if file_path.suffix.lower() != ".pdf":
        raise PdfExtractionError("uploaded report is not a PDF")

    warnings: list[str] = []
    try:
        reader = PdfReader(str(file_path))
    except Exception as exc:
        raise PdfExtractionError(f"could not read PDF: {exc}") from exc

    pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"page {index}: text extraction failed: {exc}")
            page_text = ""
        if not page_text.strip():
            warnings.append(f"page {index}: no readable text found")
        pages.append(f"\n\n--- Page {index} ---\n{page_text.strip()}")

    text = "".join(pages).strip()
    if not text:
        raise PdfExtractionError("no readable text found; this PDF may be scanned/image-only")

    return text, len(reader.pages), warnings

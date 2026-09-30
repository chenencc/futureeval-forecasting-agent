from io import BytesIO
from ForecastAgent.evidence.document import Document


def parse_pdf(raw, source):
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(raw))
    if reader.is_encrypted or len(reader.pages) > 100:
        raise ValueError("Encrypted or oversized PDF; cannot read within budget")
    documents = []
    for i, page in enumerate(reader.pages):
        stream = page.get_contents()
        if stream and len(stream.get_data()) > 10_000_000:
            raise ValueError("PDF page stream exceeds extraction budget")
        text = page.extract_text(extraction_mode="layout") or ""
        documents.append(Document(text, {"source": source, "format": "pdf", "page": i + 1}))
    if not any(d.page_content.strip() for d in documents):
        raise ValueError("Scanned/empty PDF requires OCR; no text extracted")
    return documents

from io import BytesIO
from ForecastAgent.evidence.document import Document
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def ocr_page(page):
    """Optional local OCR; bounded pages and time, no network or model call."""
    executable = shutil.which('tesseract')
    if os.environ.get('FORECAST_PDF_OCR') != '1' or not executable:
        return None
    with tempfile.TemporaryDirectory() as directory:
        image = page.to_image(resolution=120).original
        if image.width * image.height > 8_000_000:
            raise ValueError('OCR image exceeds pixel budget')
        path = Path(directory) / 'page.png'
        image.save(path)
        result = subprocess.run([executable, str(path), 'stdout'], capture_output=True, timeout=30,
                                text=True, encoding='utf-8', errors='replace', check=True)
        return result.stdout


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
    try:
        import pdfplumber
        with pdfplumber.open(BytesIO(raw)) as pdf:
            ocr_count = 0
            for index, page in enumerate(pdf.pages):
                # Limit expensive table/OCR work to a bounded prefix.
                if index >= 20:
                    documents[index].metadata['table_extraction'] = 'deferred_after_page_20'
                    continue
                tables = page.extract_tables()[:10]
                for table in tables:
                    rows = table[:200]
                    text = '\n'.join(' | '.join((cell or '').replace('\n', ' ') for cell in row) for row in rows)
                    if text.strip():
                        documents[index] = Document(documents[index].page_content + '\n[Extracted table]\n' + text[:16000], documents[index].metadata)
                documents[index].metadata.update(table_count=len(tables), table_extraction='pdfplumber',
                                                 table_extraction_warning='Heuristic cells; compare to original PDF.')
                if not documents[index].page_content.strip() and ocr_count < 3:
                    # Attempts, including empty output, consume the local page cap.
                    if os.environ.get('FORECAST_PDF_OCR')=='1' and shutil.which('tesseract'): ocr_count+=1
                    recognized = ocr_page(page)
                    if recognized:
                        documents[index] = Document(recognized, documents[index].metadata)
                        documents[index].metadata.update(extraction_method='local_tesseract_ocr',
                                                         ocr_warning='OCR may misread numbers and symbols.')
    except ImportError:
        for document in documents:
            document.metadata['table_extraction'] = 'unavailable'
    if not any(d.page_content.strip() for d in documents):
        raise ValueError("Scanned/empty PDF requires local OCR: install Tesseract and set FORECAST_PDF_OCR=1")
    for document in documents:
        if not document.page_content.strip():
            document.metadata['reading_gap'] = 'No text on this page; OCR unavailable or bounded page limit reached.'
    return documents

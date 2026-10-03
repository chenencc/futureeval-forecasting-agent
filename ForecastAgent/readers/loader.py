"""One normalized snapshot contract for existing free document readers."""
import base64
import hashlib
import re
from ForecastAgent.evidence.document import Document
from ForecastAgent.readers.html import parse_html
from ForecastAgent.readers.pdf import parse_pdf
from ForecastAgent.readers.structured import parse_csv, parse_json
from ForecastAgent.readers.feed import parse_feed
from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.readers.encoding import decode_response


def load_response(response, *, retrieved_at, max_chars=150_000, preserve_raw_on_failure=False):
    try:
        return parse_response(response,retrieved_at=retrieved_at,max_chars=max_chars)
    except Exception as exc:
        if not preserve_raw_on_failure:
            raise
        original=response['raw']
        diagnosis={}
        if response['content_type']=='application/pdf' or original.lstrip().startswith(b'%PDF-'):
            from ForecastAgent.readers.pdf_diagnostics import diagnose_pdf
            try:
                decoded,_=decode_response(original,response.get('response_headers'))
                diagnosis={'pdf_diagnosis':diagnose_pdf(decoded,response['content_type'],exc)}
            except ValueError:
                diagnosis={'pdf_diagnosis':{'schema':'pdf_diagnosis_v1','state':'transport_decoding_failure','next_action':'inspect_content_encoding','network_calls':0,'ocr_performed':False}}
        return {**diagnosis,'url':response['url'],'final_url':response['final_url'],
            'retrieved_at_utc':retrieved_at,'content_type':response['content_type'],
            'sha256':hashlib.sha256(original).hexdigest(),
            'raw_response_base64':base64.b64encode(original).decode('ascii'),
            'content':'','documents':[],'links':[],'content_truncated':False,
            'documents_truncated':False,'capture_method':'direct_http_parse_failed',
            'parse_failure':{'type':type(exc).__name__,'message':str(exc)[:300]},
            'response_headers':response.get('response_headers',{}),
            'body_diagnostics':body_diagnostics('')}


def parse_response(response, *, retrieved_at, max_chars=150_000):
    original = response['raw']
    raw, encoding = decode_response(original, response.get('response_headers'))
    source = response["final_url"]; kind = response["content_type"]
    metadata = {}; links = []; feed_truncated = False
    if raw.lstrip().startswith(b'%PDF-'):
        kind = 'application/pdf'
    if kind == "application/pdf":
        documents = parse_pdf(raw, source)
        text = "\n".join(f"[PDF page {d.metadata['page']}]\n{d.page_content}" for d in documents)
    else:
        text = raw.decode(response.get("charset", "utf-8"), errors="replace")
        if kind in {'application/rss+xml', 'application/atom+xml', 'application/xml', 'text/xml'}:
            documents, links, feed_truncated = parse_feed(raw, source)
            text = '\n\n'.join(d.page_content for d in documents)
        elif kind in {"text/html", "application/xhtml+xml"}:
            text, metadata, links = parse_html(text, source)
            documents = [Document(text, {"source": source, "format": "html"})]
            documents.extend(Document(table['text'],{'source':source,'format':'html_table','table':table['table'],
                              'table_warning':'Heuristic row extraction; merged cells may require original HTML.'}) for table in metadata.get('tables',[]))
        elif kind in {"text/csv", "application/csv"}:
            documents = parse_csv(text, source)
        elif kind == "application/json":
            documents = parse_json(text, source)
        else:
            documents = [Document(text, {"source": source, "format": "text"})]
    content = re.sub(r"[ \t]+", " ", text)
    content = re.sub(r"\n\s*\n+", "\n", content).strip()[:max_chars]
    if not content:
        raise ValueError("Page contained no readable text")
    # Original bytes always remain complete within the download byte limit.
    # Parsed documents have a shared text/count cap, not one cap per row/page.
    saved = []; remaining = max_chars
    for document in documents[:1000]:
        if remaining <= 0:
            break
        normalized = re.sub(r'\n\s*\n+', '\n', re.sub(r'[ \t]+', ' ', document.page_content)).strip()
        text_part = normalized[:remaining]; remaining -= len(text_part)
        saved.append(Document(text_part, {**document.metadata, "truncated": len(text_part) < len(normalized)}).as_dict())
    return {"url": response["url"], "final_url": source, "retrieved_at_utc": retrieved_at,
            "content_type": kind, "sha256": hashlib.sha256(original).hexdigest(),
            'declared_content_type': response['content_type'],
            "raw_response_base64": base64.b64encode(original).decode("ascii"),
            'decoded_sha256':hashlib.sha256(raw).hexdigest(), 'decoded_bytes':len(raw),
            'content_encoding':encoding, 'charset':response.get('charset', 'utf-8'),
            "page_date_metadata": metadata, "content": content,
            "content_truncated": len(text) > max_chars, "links": links,
            "capture_method": "pdf_text" if kind == "application/pdf" else "direct_http",
            "documents": saved, "document_count": len(documents),
            "parser_version": 'document_reader_v4',
            'body_diagnostics': body_diagnostics(content, kind=kind, metadata=metadata, documents=saved),
            'response_headers': response.get('response_headers', {}),
            "documents_truncated": feed_truncated or metadata.get('tables_truncated',False) or len(saved) < len(documents) or any(d["metadata"]["truncated"] for d in saved)}

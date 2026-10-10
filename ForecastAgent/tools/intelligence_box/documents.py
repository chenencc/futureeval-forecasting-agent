"""Task-scoped document limits, using existing readers without global changes."""
import hashlib
from ForecastAgent.readers.encoding import decode_response
from ForecastAgent.readers.loader import load_response


def read_document(source,raw):
    response={"raw":raw,"url":source["url"],"final_url":source["final_url"],"content_type":source["content_type"],"charset":source.get("charset","utf-8"),"response_headers":source.get("response_headers",{})}
    decoded,encoding=decode_response(raw,response["response_headers"],maximum=source.get("decoded_byte_cap",8_000_000))
    if len(decoded)<=8_000_000 or not decoded.startswith(b"%PDF-"):
        return load_response(response,retrieved_at=source["captured_at"],preserve_raw_on_failure=True)
    # The native loader has an independent 8 MB decoding default. Only a PDF
    # within this task's explicitly frozen larger cap takes the direct path.
    from ForecastAgent.readers.pdf import parse_pdf
    from ForecastAgent.readers.quality import body_diagnostics
    documents=parse_pdf(decoded,source["final_url"])
    saved=[]
    remaining=150_000
    for document in documents:
        if remaining<=0: break
        part=document.page_content[:remaining]
        remaining-=len(part)
        saved.append({"page_content":part,"metadata":dict(document.metadata,truncated=len(part)<len(document.page_content))})
    content="\n".join(d["page_content"] for d in saved)
    return {"documents":saved,"content":content,"content_type":"application/pdf","document_count":len(documents),"documents_truncated":len(saved)<len(documents) or any(d["metadata"]["truncated"] for d in saved),"capture_method":"task_scoped_pdf_text","parser_version":"intelligence_document_v1","decoded_bytes":len(decoded),"decoded_sha256":hashlib.sha256(decoded).hexdigest(),"content_encoding":encoding,"body_diagnostics":body_diagnostics(content,kind="application/pdf",documents=saved),"task_decoded_byte_cap":source["decoded_byte_cap"]}

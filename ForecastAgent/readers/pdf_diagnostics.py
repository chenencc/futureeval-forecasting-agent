"""Bounded PDF failure diagnosis from saved bytes, without OCR or network."""
from io import BytesIO

def diagnose_pdf(raw, content_type='', error=None, max_pages=5):
    report={'schema':'pdf_diagnosis_v1','byte_count':len(raw),'declared_type':content_type,'page_sample_limit':max_pages,'error':str(error)[:300] if error else None,'network_calls':0,'ocr_performed':False}
    prefix=raw.lstrip()
    if not prefix.startswith(b'%PDF-'):
        report.update(state='non_pdf_response',signature='html' if prefix[:500].lower().startswith((b'<!doctype html',b'<html')) else 'unknown',next_action='inspect_transport_or_discover_public_detail')
        return report
    report['signature']='pdf'
    if len(raw)>8_000_000:
        report.update(state='diagnostic_byte_limit',next_action='retain_bytes_and_report_limit');return report
    try:
        from pypdf import PdfReader
        reader=PdfReader(BytesIO(raw))
        if reader.is_encrypted:
            report.update(state='encrypted_pdf',next_action='discover_public_text_alternative');return report
        report['page_count']=len(reader.pages);samples=[]
        for i in range(min(len(reader.pages),max_pages)):
            page=reader.pages[i];entry={'page':i+1}
            try:
                stream=page.get_contents()
                if stream is not None and len(stream.get_data())>10_000_000:
                    entry['state']='page_stream_limit'
                else:
                    text=page.extract_text() or ''
                    entry.update(text_characters=len(text.strip()),state='text_available' if text.strip() else 'no_text_layer')
            except Exception as exc:entry.update(state='page_parse_error',error=type(exc).__name__)
            samples.append(entry)
        report['sampled_pages']=samples;report['unsampled_pages']=max(0,len(reader.pages)-len(samples))
        states={s['state'] for s in samples}
        state='text_available_parser_disagreement' if 'text_available' in states else 'page_extraction_failure' if states-{'no_text_layer'} else 'no_text_layer_in_sample' if samples else 'zero_page_pdf'
        report.update(state=state,next_action='try_alternate_text_parser' if 'text_available' in states else 'discover_public_text_or_bounded_local_ocr')
        if len(reader.pages)>100:report.update(state='page_limit_exceeded',next_action='bounded_page_range_reader_required')
    except ImportError:report.update(state='diagnostic_reader_unavailable',next_action='install_pdf_reader')
    except Exception as exc:report.update(state='malformed_or_truncated_pdf',diagnostic_error=type(exc).__name__,next_action='inspect_saved_bytes_and_public_alternative')
    return report

"""Local phrase/term navigation with stable offsets and bounded context."""
import math
import re

STOP = set('the a an of to in on at for and or is are be will would by from with what when how'.split())


def terms(text):
    tokens = re.findall(r'[a-z0-9]+(?:[./%-][a-z0-9]+)*', text.casefold())
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        tokens.extend(run[i:i+2] for i in range(max(1, len(run)-1)))
    return set(tokens) - STOP


def windows(text, width=2200, overlap=300):
    """Keep paragraphs when short; split long lines rather than discarding them."""
    for paragraph in re.finditer(r'[^\n]+', text):
        start, stop = paragraph.span()
        while start < stop:
            end = min(start + width, stop)
            if end < stop:
                boundary = text.rfind('. ', start + width // 2, end)
                if boundary > start:
                    end = boundary + 1
            yield start, end
            if end == stop:
                break
            start = max(start + 1, end - overlap)


def rank_passages(pages, query, target=None, limit=5):
    from ForecastAgent.readers.saved import documents
    candidates = []
    wanted = terms(query)
    phrase = query.strip().casefold()
    for url, page, index, doc in documents(pages):
        if target and target != url:
            continue
        text = doc['page_content']
        spans=[(0,len(text))] if doc.get('metadata',{}).get('format')=='html_table' and len(text)<=4000 else windows(text)
        for start, end in spans:
            part = text[start:end]
            found = terms(part)
            candidates.append((url, page, index, doc, start, end, part, found))
    frequency = {term: sum(term in c[-1] for c in candidates) for term in wanted}
    ranked = []
    for url, page, index, doc, start, end, part, found in candidates:
        matched = wanted & found
        exact = bool(phrase and phrase in part.casefold())
        if not matched and not exact:
            continue
        score = sum(math.log(1 + (len(candidates) + 1) / (frequency[t] + 1)) *
                    (2 if any(c.isdigit() for c in t) else 1) for t in matched)
        score = score / (1 + len(part) / 4400) + (4 if exact else 0)
        ranked.append((score, {'url': url, 'page': page, 'document_index': index, 'metadata': doc.get('metadata', {}),
                               'start_char': start, 'end_char': end, 'text': part,
                               'matched_terms': sorted(matched), 'exact_phrase': exact}))
    ranked.sort(key=lambda row: (-row[0], row[1]['url'], row[1]['document_index'], row[1]['start_char']))
    selected = []
    for _, item in ranked:
        if any(item['url'] == old['url'] and item['document_index'] == old['document_index'] and
               max(0, min(item['end_char'], old['end_char']) - max(item['start_char'], old['start_char'])) >
               min(item['end_char']-item['start_char'], old['end_char']-old['start_char']) * .6 for old in selected):
            continue
        selected.append(item)
        if len(selected) == limit:
            break
    return selected

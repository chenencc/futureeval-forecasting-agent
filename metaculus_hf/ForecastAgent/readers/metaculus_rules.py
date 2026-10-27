"""Read an explicit public resolution-criteria section without model inference."""
import base64
from datetime import datetime, timezone
from pathlib import Path

from ForecastAgent.competition.queue import load, save
from ForecastAgent.providers.ultra import fetch_public_page
from ForecastAgent.readers.browser import render_page


def extract_criteria(page, title):
    from lxml import html
    document = html.fromstring(base64.b64decode(page['raw_response_base64'], validate=True))
    visible = ' '.join(document.xpath('//body//text()[not(ancestor::script) and not(ancestor::style)]'))
    if ' '.join(title.split()).casefold() not in ' '.join(visible.split()).casefold():
        raise ValueError('Official page title does not match the selected question')
    headings = document.xpath('//h1|//h2|//h3|//h4|//h5|//h6')
    matches = [heading for heading in headings if ' '.join(heading.itertext()).strip().casefold() == 'resolution criteria']
    if len(matches) != 1:
        raise ValueError('Exactly one explicit Resolution Criteria heading required')
    heading = matches[0]
    paragraphs = []
    for sibling in heading.itersiblings():
        if sibling.tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6') or sibling.xpath('.//h1|.//h2|.//h3|.//h4|.//h5|.//h6'):
            break
        if sibling.tag in ('script', 'style', 'nav', 'button'):
            continue
        text = ' '.join(' '.join(sibling.itertext()).split())
        if text:
            paragraphs.append(text)
    criteria = '\n'.join(paragraphs)
    if not criteria or len(criteria) > 10000:
        raise ValueError('Explicit rule section is empty or exceeds safe extraction bounds')
    return criteria


def read_rules(root, post_id, title):
    """One free HTTP attempt plus one public browser attempt, persisted on failure."""
    root = Path(root)
    ledger = root / 'rule-read-attempts.json'
    attempts = load(ledger) if ledger.exists() else []
    url = f'https://www.metaculus.com/questions/{int(post_id)}/'
    for method in ('http', 'browser'):
        previous = next((a for a in attempts if a['method'] == method), None)
        if previous:
            if previous.get('status') == 'received':
                return extract_criteria(load(root / previous['file']), title)
            continue
        record = {'method': method, 'url': url, 'status': 'reserved'}
        attempts.append(record)
        save(ledger, attempts)
        try:
            page = (fetch_public_page(url, preserve_raw_on_failure=True) if method == 'http' else
                    render_page(url, retrieved_at=datetime.now(timezone.utc).isoformat()))
            file = f'official-rules-{method}.json'
            save(root / file, page)
            criteria = extract_criteria(page, title)
            record.update(status='received', file=file, capture_sha256=page.get('sha256'))
            save(ledger, attempts)
            return criteria
        except Exception as exc:
            record.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            save(ledger, attempts)
    raise ValueError('Full resolution criteria unavailable from API and bounded public-page readers')

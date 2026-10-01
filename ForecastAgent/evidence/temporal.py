"""Describe time provenance without inferring truth or historical page versions."""
from datetime import datetime


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def temporal_report(bundle):
    cutoff = timestamp(bundle['request'].get('as_of_utc'))
    rows = []
    def add(page, kind, url=None):
        if not isinstance(page, dict):
            return
        captured = timestamp(page.get('retrieved_at_utc') or page.get('retrieved_at'))
        published = timestamp(page.get('published_at') or page.get('published_date'))
        updated = timestamp(page.get('updated_at'))
        snapshot_time=timestamp(page.get('archive_timestamp')) if page.get('temporal_status')=='archive_pre_cutoff_capture' else captured
        strict = bool(cutoff and snapshot_time and snapshot_time < cutoff and
                      page.get('temporal_status') in {'local_pre_cutoff_capture','archive_pre_cutoff_capture'})
        category = ('quarantined' if kind == 'quarantine' else
                    'pre_cutoff_snapshot' if strict else
                    'live_capture' if not cutoff else
                    'after_cutoff_metadata' if any(t and t >= cutoff for t in (published, updated)) else
                    'current_capture_of_historical_material' if published else 'unknown_publication')
        rows.append({'url': url or page.get('url'), 'kind': kind, 'category': category,
                     'captured_at': captured.isoformat() if captured else None,
                     'published_at': published.isoformat() if published else None,
                     'updated_at': updated.isoformat() if updated else None,
                     'captured_after_cutoff': bool(cutoff and captured and captured > cutoff),
                     'strict_snapshot_eligible': strict,
                     'archive_timestamp':page.get('archive_timestamp'),
                     'original_temporal_status': page.get('temporal_status'),
                     'limitation': 'Publication metadata does not establish a historical body version.'})
    for url, page in bundle.get('pages', {}).items():
        add(page, 'page', url)
    for url, versions in bundle.get('page_history', {}).items():
        for page in versions:
            add(page, 'page_version', url)
    for search in bundle.get('searches', []) + bundle.get('exa_searches', []):
        for hit in search.get('results', []):
            add(hit, 'search_lead')
    for item in bundle.get('quarantine', []):
        add(item.get('page_snapshot') or item.get('hit') or item, 'quarantine')
    return {'schema': 'temporal_provenance_v1', 'as_of_utc': bundle['request'].get('as_of_utc'),
            'historical_body_policy':bundle.get('historical_body_policy'),
            'question_text_audit': bundle['request'].get('historical_criteria_audit', 'Not audited'),
            'historical_clean': False, 'model_knowledge_leakage_controlled': False,
            'records': rows}

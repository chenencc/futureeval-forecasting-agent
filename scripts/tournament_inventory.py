"""Read-only 2026 seasonal inventory; no forecasting, search or LLM calls."""
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from scripts.monitor_tournament import API_ROOT, collect_pages, _questions
from scripts.ultra_research_agent import utc_now

SEASONS = ['spring-aib-2026', 'summer-futureeval-2026', 'fall-futureeval-2026']


def main():
    root = Path('snapshots/tournament-inventory-2026')
    root.mkdir(parents=True, exist_ok=True)
    manifest = []
    for season in SEASONS:
        url = API_ROOT+'?'+urlencode({'tournaments': season, 'limit': 100, 'include_description': 'true', 'order_by': 'id'})
        posts, cursor, pages = collect_pages(os.environ['METACULUS_TOKEN'], url, 100)
        if cursor:
            raise RuntimeError('Inventory incomplete; more pages remain')
        unique = {p['id']: p for p in posts}
        payload = {'downloaded_at_utc': utc_now(), 'season': season, 'posts': list(unique.values()), 'complete': True, 'api_pages': pages}
        (root/(season+'.json')).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        row = {'season': season, 'posts': len(unique), 'question_posts': sum(bool(_questions(p)) for p in unique.values()), 'question_leaves': sum(len(_questions(p)) for p in unique.values()), 'api_pages': pages}
        manifest.append(row)
        print(json.dumps(row), flush=True)
    (root/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()

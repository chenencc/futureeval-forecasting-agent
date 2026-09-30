"""Frozen selection of three open questions; real Ultra collection, no forecasts."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from ForecastAgent.agent import run_research
from ForecastAgent.monitor_tournament import API_ROOT, collect_questions, get_json, _questions, _write_json
from ForecastAgent.evidence.acceptance import collection_acceptance

ROOT = Path('snapshots/live-collection')


def eligible(post, question):
    if post.get('status') != 'open' or question.get('status', 'open') != 'open' or question.get('type') != 'binary':
        return False
    if question.get('resolution') is not None:
        return False
    close = question.get('scheduled_close_time') or post.get('scheduled_close_time')
    if close:
        try:
            if datetime.fromisoformat(close.replace('Z', '+00:00')) <= datetime.now(timezone.utc) + timedelta(days=1):
                return False
        except (ValueError, TypeError):
            return False
    return bool(question.get('resolution_criteria') or post.get('resolution_criteria'))


def category(title):
    text = title.lower()
    if any(word in text for word in ('gdp', 'inflation', 'treasury', 'interest rate', 'fed ', 'unemployment', 'oil price', 'stock')):
        return 'economics'
    if any(word in text for word in ('openai', 'anthropic', ' model', ' ai ', 'benchmark', 'gpu')):
        return 'technology'
    if any(word in text for word in ('election', 'president', 'minister', 'legislation', 'government')):
        return 'government'
    return 'other'


def select_questions(posts, excluded, count=3):
    selected = []; seen_categories = set(); pool = []
    seen_ids = set()
    for post in posts:
        for question in _questions(post):
            if question['id'] in excluded or question['id'] in seen_ids or not eligible(post, question):
                continue
            title = question.get('title') or post.get('title') or ''
            row = {'post_id': post['id'], 'question_id': question['id'], 'category': category(title),
                   'url': f"https://www.metaculus.com/questions/{post['id']}/",
                   'request': {'question': title, 'resolution_criteria': question.get('resolution_criteria') or post['resolution_criteria'],
                               'fine_print': question.get('fine_print') or post.get('fine_print') or '',
                               'background': post.get('description') or '', 'mode': 'live', 'pipeline': 'collection'}}
            seen_ids.add(question['id']); pool.append(row)
    for row in pool:
        if row['category'] not in seen_categories:
            selected.append(row); seen_categories.add(row['category'])
        if len(selected) == count:
            break
    for row in pool:
        if len(selected) == count:
            break
        if row not in selected:
            selected.append(row)
    return selected


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    token = os.environ['METACULUS_TOKEN']
    manifest = ROOT / 'selection.json'
    if manifest.exists():
        selected = json.loads(manifest.read_text(encoding='utf-8'))['questions']
    else:
        tournament, complete = collect_questions(token)
        if not complete:
            raise RuntimeError('Tournament exclusion inventory is incomplete')
        excluded = {q['id'] for p in tournament for q in _questions(p)}
        monitor = ROOT / 'monitor-state' / 'retrieval'
        excluded.update(int(p.name) for p in monitor.iterdir() if p.is_dir() and p.name.isdigit()) if monitor.exists() else None
        url = API_ROOT + '?' + urlencode({'statuses': 'open', 'forecast_type': 'binary', 'limit': 100,
                                          'include_description': 'true', 'order_by': '-hotness'})
        posts = get_json(url, token)['results']
        _write_json(ROOT / 'open-posts.json', {'captured_at': datetime.now(timezone.utc).isoformat(), 'posts': posts})
        selected = select_questions(posts, excluded)
        if len(selected) != 3:
            raise RuntimeError('Fewer than three eligible open binary questions outside monitored tasks')
        _write_json(manifest, {'selected_at': datetime.now(timezone.utc).isoformat(), 'questions': selected,
                              'policy': 'Exclude current tournament and existing monitor ledgers; freeze selection and budgets.'})
    fresh = get_json(API_ROOT + '?' + urlencode({'statuses':'open', 'forecast_type':'binary', 'limit':100,
                                                'include_description':'true', 'order_by':'-hotness'}), token)['results']
    open_ids = {q['id'] for p in fresh for q in _questions(p) if eligible(p, q)}
    summaries = []
    for row in selected:
        ident = str(row['question_id'])
        directory = ROOT / 'tasks' / ident
        if row['question_id'] not in open_ids:
            detail = get_json(API_ROOT + str(row['post_id']) + '/', token)
            if any(q['id'] == row['question_id'] and eligible(detail, q) for q in _questions(detail)):
                open_ids.add(row['question_id'])
        if row['question_id'] not in open_ids:
            summaries.append({'question_id': row['question_id'], 'status': 'skipped_not_currently_eligible'})
            continue
        print('COLLECT', row['question_id'], row['request']['question'], flush=True)
        try:
            report = run_research(row['request'], directory)
            summary = {'question_id': row['question_id'], 'url': row['url'], 'question': row['request']['question'],
                       'status': report['result']['status'], 'incomplete': report['result'].get('incomplete', False),
                       'search_attempts': len(report['searches']), 'initial_http_attempts': len(report['fetch_attempts']),
                       'pages': len(report['pages']), 'excerpts': len(report['excerpts']),
                       'market_snapshots': len(report['market_snapshots']), 'acceptance': collection_acceptance(report),
                       'tools_used': sorted({t['tool'] for t in report['transcript']}), 'last_error': report.get('last_error_detail')}
        except Exception as exc:
            summary = {'question_id': row['question_id'], 'status': 'error', 'error_type': type(exc).__name__}
        summaries.append(summary)
        _write_json(ROOT / 'summary.json', summaries)
        print(json.dumps({k: summary.get(k) for k in ('question_id','status','incomplete','pages','excerpts','search_attempts')}), flush=True)
    _write_json(ROOT / 'summary.json', summaries)
    if len(summaries) != 3 or any(s.get('incomplete') or s['status'] in ('error', 'skipped_not_currently_eligible') for s in summaries):
        raise RuntimeError('Collection incomplete; resume the same workflow to retain selection and budgets')


if __name__ == '__main__':
    main()

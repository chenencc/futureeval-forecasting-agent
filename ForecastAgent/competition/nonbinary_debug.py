import json, os, pathlib, urllib.request, urllib.error
from datetime import datetime, timezone
token = os.environ.get('METACULUS_TOKEN', '')
report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'secret_present': bool(token), 'requests': [], 'submission_attempted': False}
def get(path):
    url = 'https://www.metaculus.com/api/' + path
    request = urllib.request.Request(url, headers={'Authorization': 'Token ' + token, 'Accept': 'application/json', 'User-Agent': 'ForecastAgent-readonly-permission-audit/1.0'}, method='GET')
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.load(response)
            report['requests'].append({'path': path, 'method': 'GET', 'status': response.status})
            return data
    except urllib.error.HTTPError as exc:
        report['requests'].append({'path': path, 'method': 'GET', 'status': exc.code})
    except Exception as exc:
        report['requests'].append({'path': path, 'method': 'GET', 'error_type': type(exc).__name__})
    return None
if token:
    account = get('users/me/?with_data_access=true')
    if isinstance(account, dict):
        report['account'] = {k: account.get(k) for k in ('id', 'username', 'is_bot', 'is_active', 'is_primary_bot', 'api_forecasting_access', 'api_access_tier', 'project_data_access')}
    def inspect(post):
        questions = []
        if isinstance(post.get('question'), dict): questions.append(post['question'])
        group = post.get('group_of_questions') or {}
        questions.extend(group.get('questions') or [])
        return {'post_id': post.get('id'), 'status': post.get('status'), 'user_permission': post.get('user_permission'), 'questions': [{'id': q.get('id'), 'type': q.get('type'), 'status': q.get('status'), 'description_available': bool(q.get('description')), 'resolution_criteria_available': bool(q.get('resolution_criteria'))} for q in questions], 'tournaments': [{'id': p.get('id'), 'slug': p.get('slug'), 'user_permission': p.get('user_permission')} for p in (post.get('projects') or {}).get('tournament', [])]}
    closed = get('posts/45849/?include_descriptions=true&with_cp=false')
    if isinstance(closed, dict): report['closed_post'] = inspect(closed)
    feed = get('posts/?tournaments=fall-futureeval-2026&statuses=open&limit=3&include_descriptions=true&with_cp=false')
    if isinstance(feed, dict):
        report['open_feed'] = {'count': feed.get('count'), 'posts': [inspect(p) for p in feed.get('results', [])]}
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--root', default='snapshots/nonbinary-debug')
args, _ = parser.parse_known_args()
pathlib.Path(args.root).mkdir(parents=True, exist_ok=True)
(pathlib.Path(args.root) / 'token-permission-audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))

"""Track every observed question without conflating discovery and forecasting."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

TOURNAMENT = 'fall-futureeval-2026'
SCHEMA = 'competition-shadow-v1'


def utc(value=None):
    parsed = datetime.now(timezone.utc) if value is None else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include a timezone')
    return parsed.astimezone(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def questions(post):
    direct = [post['question']] if post.get('question') else []
    return direct + (post.get('group_of_questions') or {}).get('questions', [])


def descriptor(post, question):
    """Whitelist rules and timing; exclude outcomes and community predictions."""
    result = {key: question.get(key) for key in ('id', 'type', 'status', 'open_time',
              'close_time', 'scheduled_close_time', 'spot_scoring_time')}
    result['post_id'] = post['id']
    for key in ('title', 'resolution_criteria', 'fine_print'):
        result[key] = question.get(key) or post.get(key) or ''
    result['rule_sha256'] = digest({key: result[key] for key in ('title', 'resolution_criteria', 'fine_print', 'type')})
    deadlines = [utc(result[key]) for key in ('close_time', 'scheduled_close_time', 'spot_scoring_time') if result.get(key)]
    result['deadline_utc'] = min(deadlines).isoformat() if deadlines else None
    return result


class Queue:
    """Caller holds the queue directory lock for the full transaction."""
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'queue.json'
        self.state = load(self.path) if self.path.exists() else {
            'schema': SCHEMA, 'tournament': TOURNAMENT, 'tasks': {}, 'observations': [],
            'no_forecasts_submitted': True}
        if self.state.get('schema') != SCHEMA or self.state.get('tournament') != TOURNAMENT:
            raise ValueError('Unrecognized competition state; refuse migration or reset')
        if not self.path.exists() and (self.root / 'tasks').exists():
            raise ValueError('Task files exist without queue; refuse budget reset')

    def commit(self):
        save(self.path, self.state)

    def event(self, task, stage, reason, now):
        task['stage'] = stage
        task.setdefault('history', []).append({'at_utc': utc(now).isoformat(), 'stage': stage, 'reason': reason})

    def ingest(self, snapshot, now=None):
        snapshot = Path(snapshot)
        index = load(snapshot / 'index.json')
        if index.get('tournament') != TOURNAMENT or index.get('open_scan_complete') is not True:
            raise ValueError('A complete open scan for the configured tournament is required')
        observed = utc(index['retrieved_at_utc'])
        current = utc(now)
        if observed > current:
            raise ValueError('Snapshot timestamp is in the future')
        # Validate every row before changing the queue.
        validated = []
        seen = set()
        for row in index['questions']:
            ident = str(row['question_id'])
            if not ident.isdecimal() or ident in seen:
                raise ValueError('Invalid or duplicate question ID in snapshot')
            seen.add(ident)
            payload = load(snapshot / f"{int(row['post_id'])}.json")
            post = payload['post']
            matches = [q for q in questions(post) if str(q['id']) == ident]
            if post['id'] != row['post_id'] or len(matches) != 1:
                raise ValueError('Snapshot post/question identity mismatch')
            desc = descriptor(post, matches[0])
            if row.get('type') != desc['type'] or row.get('status') != desc['status']:
                raise ValueError('Snapshot index disagrees with raw question')
            validated.append((ident, desc, payload))
        for ident, desc, payload in validated:
            task = self.state['tasks'].get(ident)
            if task and utc(task['observed_at_utc']) > observed:
                continue
            if not task:
                task = {'id': ident, 'discovered_at_utc': observed.isoformat(), 'history': [], 'attempts': 0}
                self.state['tasks'][ident] = task
            changed = bool(task.get('question') and task['question']['rule_sha256'] != desc['rule_sha256'])
            task.update(question=desc, observed_at_utc=observed.isoformat())
            snapshot_hash = digest(payload)
            save(self.root / 'snapshots' / ident / f'{snapshot_hash}.json', payload)
            task['snapshot_sha256'] = snapshot_hash
            if changed:
                self.event(task, 'blocked_integrity', 'Question rules changed; prior journals remain frozen', now)
            elif desc['status'] in ('closed', 'resolved', 'canceled'):
                self.event(task, 'closed', 'Platform question is not open', now)
            elif desc['deadline_utc'] and utc(desc['deadline_utc']) <= current:
                self.event(task, 'deadline_missed', 'Scoring/close deadline already passed', now)
            elif desc['type'] != 'binary':
                self.event(task, 'unsupported_type', 'Release 1.0 analysis only supports binary questions', now)
            elif not desc['deadline_utc']:
                self.event(task, 'blocked_integrity', 'No usable scoring or close deadline', now)
            elif not task.get('stage'):
                self.event(task, 'awaiting_collection', 'Existing collector owns retrieval and search budgets', now)
        self.state['observations'].append({'at_utc': observed.isoformat(), 'index_sha256': digest(index),
            'question_count': len(validated), 'archive_cycle_complete': index.get('archive_cycle_complete') is True})
        self.commit()

    def pending(self, now=None):
        current = utc(now)
        result = []
        for task in self.state['tasks'].values():
            if task['stage'] not in ('awaiting_collection', 'supplementing', 'analyzing', 'retry_wait'):
                continue
            deadline = utc(task['question']['deadline_utc'])
            if deadline <= current:
                self.event(task, 'deadline_missed', 'Deadline passed while waiting or recovering', now)
            elif task.get('retry_at_utc') and utc(task['retry_at_utc']) > current:
                continue
            elif task['question']['status'] == 'open':
                result.append(task)
        self.commit()
        return sorted(result, key=lambda task: (task['question']['deadline_utc'], task['id']))

    def report(self):
        counts = {}
        for task in self.state['tasks'].values():
            counts[task['stage']] = counts.get(task['stage'], 0) + 1
        return {'schema': SCHEMA, 'question_count': len(self.state['tasks']), 'stages': counts,
                'no_forecasts_submitted': True, 'automatic_submission_enabled': False}

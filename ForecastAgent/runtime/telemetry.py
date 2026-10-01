"""Durable transport records; credentials and authorization headers are excluded."""
import hashlib
import json
from pathlib import Path

MAX_MODEL_ATTEMPTS = 72
MAX_RUN_SECONDS = 900


def model_observer(task, secrets=(), dispatch_limit=None, failure_limit=None):
    starting_attempts = len(task.bundle.get('model_attempts',[]))
    def observe(event, record, token=None):
        attempts = task.bundle.setdefault('model_attempts', [])
        if event == 'reserve':
            failed = sum(a.get('status') != 'received' for a in attempts[starting_attempts:])
            if failure_limit is not None and failed >= failure_limit:
                raise RuntimeError('Model transport failure allowance exhausted; preserve state for later recovery')
            if dispatch_limit is not None and len(attempts)-starting_attempts >= dispatch_limit:
                raise RuntimeError('Physical model HTTP dispatch budget exhausted')
            if len(attempts) >= MAX_MODEL_ATTEMPTS:
                raise RuntimeError('Lifetime model attempt budget exhausted (72)')
            token = len(attempts)
            attempts.append({'id': token + 1, 'status': 'reserved',
                             'started_at_utc': record['started_at_utc'],
                             'retry_index': record['retry_index']})
        serialized = json.dumps(record, ensure_ascii=False)
        for secret in secrets:
            if secret:
                serialized = serialized.replace(secret, '[REDACTED]')
        root = task.directory / 'model_calls'
        root.mkdir(exist_ok=True)
        path = root / f'{token + 1:04d}.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(serialized, encoding='utf-8')
        temporary.replace(path)
        attempts[token].update(status=record['status'], path=str(path.relative_to(task.directory)),
            sha256=hashlib.sha256(serialized.encode()).hexdigest(),
            duration_seconds=record.get('duration_seconds'),
            usage=(record.get('response') or {}).get('usage') if isinstance(record.get('response'), dict) else None)
        task.save()
        return token
    return observe

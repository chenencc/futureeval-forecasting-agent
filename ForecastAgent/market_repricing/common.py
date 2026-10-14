"""Atomic, content-addressed research state and bounded public transports."""
import hashlib
import gzip
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import PROTOCOL, ENGINE_BRANCH, ENGINE_SHA


def now():
    return datetime.now(timezone.utc).isoformat()


def instant(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone is required')
    return result.astimezone(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    os.replace(temp, path)


def policy():
    return read(Path(__file__).with_name('policy.json'))


def state(root, role, configuration):
    path = Path(root) / 'state.json'
    identity = {'protocol': PROTOCOL, 'role': role, 'policy_sha256': digest(configuration),
                'engine_branch': ENGINE_BRANCH, 'engine_sha': ENGINE_SHA,
                'development_only': True, 'release_fallback': False}
    if path.exists():
        saved = read(path)
        # Exact authorized operational-cap migration, never a ledger reset.
        # Older admission migrations still run against their original 400 policy.
        if (saved.get('identity') != identity and digest(configuration) ==
                '5af20b76a50914620c01a50b29353be41f7cf9a87d4eeeb70c8faaa21551e84e'):
            old = dict(configuration, maximum_worker_model_attempts_per_utc_day=400)
            saved = state(root, role, old)
            previous = saved['identity']
            saved.setdefault('policy_migrations', []).append({'at': now(),
                'from_identity': previous, 'to_identity': identity,
                'reason': 'Authorized local model daily ceiling 400 to 900; physical usage retained.',
                'provider_reservations_unchanged': True, 'existing_tasks_unchanged': True,
                'budget_reset': False})
            saved['identity'] = identity
        if saved.get('identity') != identity:
            # Only exact previously deployed admission policies may migrate.
            # Engine, reservations and every provider cap remain unchanged.
            admission_previous = dict(configuration)
            admission_previous.pop('enrollment_window_hours', None)
            for field in ('maximum_candidate_age_hours', 'price_target_origin',
                          'maximum_resolution_requests_per_dispatch', 'final_refresh_interval_hours',
                          'resolved_recheck_interval_hours'):
                admission_previous.pop(field, None)
            admission_previous.update(maximum_candidate_age_days=1,
                                      maximum_new_tasks_per_day=20, horizons_hours=[72, 168])
            previous_daily = dict(admission_previous)
            previous_daily.pop('allow_wide_spread', None)
            legacy = dict(previous_daily)
            legacy.pop('enrollment_day_timezone', None)
            legacy.pop('maximum_new_tasks_per_day', None)
            legacy.update(maximum_active_families=3, maximum_new_tasks_per_utc_day=3)
            approved_hashes = {'315c683957751c19000d0a307a52a4bc664ac682dd07bcdea4413cea578333b9',
                               '642119b33adddb5331834c728c5ae43a0a4e22f742e104891e388b7e47ae96df',
                               '31ef70793256bce9ca689e0d51911e05fe8288fa1aedffd8661487c81d423825'}
            rolling_previous = dict(configuration, maximum_new_tasks_per_day=30)
            authorized_fifty = (configuration.get('maximum_new_tasks_per_day') == 50
                and configuration.get('enrollment_window_hours') == 24
                and digest(rolling_previous) == '7c60c698059b659a0e3111f49592dbf0d00daecda14e4a1976d49bf27346b0c1')
            calendar_previous = dict(rolling_previous if authorized_fifty else configuration)
            calendar_previous.pop('enrollment_window_hours', None)
            if configuration.get('enrollment_window_hours') == 24:
                approved_hashes.add('d72c87273d613240e98f8e6f0fc1022e4c462db0c5b6c022bdf7a4e55054d7c2')
            if authorized_fifty:
                approved_hashes.add('7c60c698059b659a0e3111f49592dbf0d00daecda14e4a1976d49bf27346b0c1')
            previous = next((identity | {'policy_sha256': digest(candidate)}
                for candidate in (admission_previous, previous_daily, legacy, calendar_previous, rolling_previous)
                if digest(candidate) in approved_hashes and saved.get('identity') == identity | {'policy_sha256': digest(candidate)}), None)
            expected = {'maximum_new_tasks_per_day': 50 if authorized_fifty else 30, 'maximum_candidate_age_hours': 2,
                'horizons_hours': [24, 72, 168], 'price_target_origin': 'market_created_at',
                'maximum_resolution_requests_per_dispatch': 20, 'final_refresh_interval_hours': 6,
                'resolved_recheck_interval_hours': 24, 'enrollment_day_timezone': 'Asia/Shanghai',
                'allow_wide_spread': True}
            if 'enrollment_window_hours' in configuration:
                expected['enrollment_window_hours'] = 24
            if previous is None or any(configuration.get(k) != v for k, v in expected.items()):
                raise ValueError('State identity changed; explicit migration is required')
            saved.setdefault('policy_migrations', []).append({
                'at': now(), 'from_identity': previous, 'to_identity': identity,
                'reason': 'User authorized fifty admissions per rolling 24 hours; existing slots, tasks and provider budgets retained.'
                    if authorized_fifty else
                    'User authorized at most thirty families per rolling 24 hours; two-hour admission and creation-based observations retained.'
                    if configuration.get('enrollment_window_hours') == 24 else
                    'User authorized thirty daily families, two-hour admission and creation-based 1/3/7-day observations.',
                'existing_tasks_unchanged': True, 'legacy_forecast_targets_unchanged': True,
                'provider_reservations_unchanged': True, 'legacy_daily_counts_retained': True,
                'budget_reset': False})
            saved['identity'] = identity
        return saved
    if Path(root).exists() and any(Path(root).iterdir()):
        raise ValueError('Files without a ledger; refuse bootstrap and budget reset')
    return {'identity': identity, 'created_at': now(), 'markets': {}, 'tasks': {}, 'daily': {}}


def seal(root):
    root = Path(root)
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink in research state')
        if path.is_file() and path.name != 'inventory.json' and not path.name.endswith('.tmp'):
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    write(root / 'inventory.json', {'schema': PROTOCOL, 'files': files})


def verify(root):
    root = Path(root)
    inventory = read(root / 'inventory.json')
    if inventory.get('schema') != PROTOCOL or 'state.json' not in inventory['files']:
        raise ValueError('Research inventory is missing its ledger')
    for name, expected in inventory['files'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Research checkpoint integrity failure: ' + name)
    return len(inventory['files'])


def number(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a numeric quote')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Nonfinite number')
    return result


class PublicHTTP:
    """No implicit retry. Save reservations and bounded response bytes."""
    HOSTS = ('https://gamma-api.polymarket.com/', 'https://clob.polymarket.com/',
             'https://external-api.kalshi.com/trade-api/v2/')

    def __init__(self, root, cap=100):
        self.root = Path(root)
        self.cap = cap
        self.calls = 0

    def get(self, base, params=None):
        if not any(base.startswith(host) for host in self.HOSTS):
            raise ValueError('Unexpected public market endpoint')
        if self.calls >= self.cap:
            raise RuntimeError('Public HTTP dispatch cap exhausted')
        self.calls += 1
        url = base + ('?' + urlencode(params, doseq=True) if params else '')
        receipt = {'url': url, 'reserved_at': now(), 'status': 'reserved'}
        ident = digest([url, receipt['reserved_at'], self.calls])
        path = self.root / (ident + '.json')
        write(path, receipt)
        try:
            # Redirects must stay within the public endpoint allowlist.
            with urlopen(Request(url, headers={'User-Agent': 'ForecastAgent repricing research/1.0'}), timeout=30) as response:
                if not any(response.geturl().startswith(host) for host in self.HOSTS):
                    raise ValueError('Unexpected endpoint redirect')
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('Market response exceeds byte limit')
                receipt.update(http_status=response.status, response_sha256=hashlib.sha256(raw).hexdigest(),
                               status='received', received_at=now())
                parsed = json.loads(raw)
                object_path = self.root / 'objects' / (receipt['response_sha256'] + '.json.gz')
                if not object_path.exists():
                    object_path.parent.mkdir(parents=True, exist_ok=True)
                    object_path.write_bytes(gzip.compress(raw, mtime=0))
                receipt['response_object'] = 'objects/' + object_path.name
            return parsed, {'receipt': path.name, 'response_sha256': receipt['response_sha256'],
                                         'received_at': receipt['received_at']}
        except Exception as exc:
            receipt.update(status='failed', error_type=type(exc).__name__, error_detail=str(exc)[:240],
                           http_status=getattr(exc, 'code', None))
            raise RuntimeError('Public endpoint failure: ' + type(exc).__name__) from exc
        finally:
            write(path, receipt)

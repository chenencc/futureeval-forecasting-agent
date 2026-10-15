"""Atomic local objects and a versioned engineering database."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3


def now():
    return datetime.now(timezone.utc).isoformat()


def instant(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone is required')
    return result.astimezone(timezone.utc)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_bytes(encoded(value))
    temporary.replace(path)


def read(path, limit=64 * 1024**2):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError('Object exceeds declared reading bound')
    return json.loads(path.read_bytes())


def safe_metadata(value):
    """Metadata is allowlisted at callers; forbid credential-bearing keys too."""
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(r'api.?key|authorization|password|secret|cookie|access.?token', key, re.I):
                raise ValueError('Credentials are not engineering metadata')
            safe_metadata(child)
    elif isinstance(value, list):
        for child in value:
            safe_metadata(child)
    elif isinstance(value, str) and re.search(r'(?:sk-or-v1-|tvly-)[a-zA-Z0-9_-]{8,}|\bBearer\s+\S+', value):
        raise ValueError('Credential values are not engineering metadata')
    encoded(value)
    return value


def connect(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'engineering.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, 1):
        db.close()
        raise ValueError('Unsupported engineering schema version')
    db.executescript('''
    BEGIN IMMEDIATE;
    CREATE TABLE IF NOT EXISTS objects(sha256 TEXT PRIMARY KEY, bytes BLOB NOT NULL);
    CREATE TABLE IF NOT EXISTS traces(id TEXT PRIMARY KEY,domain TEXT NOT NULL,task_key TEXT NOT NULL,
      dispatch_id TEXT NOT NULL,started_at TEXT NOT NULL,metadata TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS spans(id TEXT PRIMARY KEY,trace_id TEXT NOT NULL REFERENCES traces(id),
      parent_id TEXT REFERENCES spans(id),stage TEXT NOT NULL,status TEXT NOT NULL,
      started_at TEXT NOT NULL,finished_at TEXT,duration_ms REAL,error_code TEXT,usage TEXT,metadata TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS lifecycle(id TEXT PRIMARY KEY,domain TEXT NOT NULL,task_key TEXT NOT NULL,
      stage TEXT NOT NULL,state TEXT NOT NULL,occurred_at TEXT NOT NULL,retry_at TEXT,
      failure_code TEXT,source_sha256 TEXT NOT NULL,data TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS lifecycle_task ON lifecycle(domain,task_key,stage,occurred_at);
    CREATE TABLE IF NOT EXISTS experiments(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,
      manifest_sha256 TEXT NOT NULL REFERENCES objects(sha256),created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cases(experiment_id TEXT NOT NULL REFERENCES experiments(id),
      case_id TEXT NOT NULL,input_sha256 TEXT NOT NULL REFERENCES objects(sha256),
      PRIMARY KEY(experiment_id,case_id));
    CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,experiment_id TEXT NOT NULL,case_id TEXT NOT NULL,
      route_id TEXT NOT NULL,mode TEXT NOT NULL,status TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,
      output_sha256 TEXT REFERENCES objects(sha256),error_code TEXT,usage TEXT,
      FOREIGN KEY(experiment_id,case_id) REFERENCES cases(experiment_id,case_id),
      UNIQUE(experiment_id,case_id,route_id,mode));
    CREATE TABLE IF NOT EXISTS labels(id TEXT PRIMARY KEY,experiment_id TEXT NOT NULL,case_id TEXT NOT NULL,
      kind TEXT NOT NULL,observed_at TEXT NOT NULL,data TEXT NOT NULL,
      FOREIGN KEY(experiment_id,case_id) REFERENCES cases(experiment_id,case_id));
    PRAGMA user_version=1;
    COMMIT;
    ''')
    return db


def put(db, value):
    raw = encoded(value)
    sha = hashlib.sha256(raw).hexdigest()
    db.execute('INSERT OR IGNORE INTO objects VALUES(?,?)', (sha, raw))
    return sha


def get(db, sha):
    row = db.execute('SELECT bytes FROM objects WHERE sha256=?', (sha,)).fetchone()
    if row is None:
        raise KeyError('Frozen object missing')
    if hashlib.sha256(row[0]).hexdigest() != sha:
        raise ValueError('Frozen object integrity failure')
    return json.loads(row[0])

"""Bounded, resumable artifact ranges; credentials never reach storage hosts."""
import hashlib
import io
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
import zipfile


MAX_RANGE = 1024 * 1024
MAX_DIRECTORY = 16 * 1024 * 1024


class TransferDeferred(RuntimeError):
    """A local traffic limit paused transfer without consuming research budgets."""


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    temporary.replace(path)


class TrafficBudget:
    """Reserve payload bytes before a request, including failed/partial requests.

    GitHub JSON metadata and protocol headers are excluded. Reservations are a
    conservative bound: uncertain interrupted requests are not refunded.
    """
    def __init__(self, root, daily_bytes, monthly_bytes=0, clock=None):
        from datetime import datetime, timezone
        if type(daily_bytes) is not int or type(monthly_bytes) is not int or daily_bytes < 0 or monthly_bytes < 0:
            raise ValueError('Local traffic limits must be nonnegative integer bytes')
        self.path = Path(root) / 'transfer-usage.json'
        self.daily_bytes = daily_bytes
        self.monthly_bytes = monthly_bytes
        self.clock = clock or (lambda: datetime.now(timezone.utc).astimezone().isoformat())
        self.ledger = json.loads(self.path.read_text()) if self.path.exists() else {
            'schema': 'forecast-transfer-usage-v1', 'days': {}, 'months': {}}
        self.downloaded = 0
        self.cache_bytes = 0
        self.uncertain_ranges = 0

    def reserve(self, size):
        if size <= 0:
            return
        stamp = self.clock()
        day, month = stamp[:10], stamp[:7]
        if (self.daily_bytes and self.ledger['days'].get(day, 0) + size > self.daily_bytes or
                self.monthly_bytes and self.ledger['months'].get(month, 0) + size > self.monthly_bytes):
            raise TransferDeferred('Local artifact traffic limit reached; saved ranges retained')
        self.ledger['days'][day] = self.ledger['days'].get(day, 0) + size
        self.ledger['months'][month] = self.ledger['months'].get(month, 0) + size
        atomic_json(self.path, self.ledger)

    def snapshot(self):
        stamp = self.clock()
        return {'payload_bytes_received_this_sync': self.downloaded,
                'cached_range_bytes_read_this_sync': self.cache_bytes,
                'reserved_bytes_today': self.ledger['days'].get(stamp[:10], 0),
                'reserved_bytes_this_month': self.ledger['months'].get(stamp[:7], 0),
                'daily_limit_bytes': self.daily_bytes, 'monthly_limit_bytes': self.monthly_bytes,
                'payload_byte_count_complete': self.uncertain_ranges == 0,
                'uncertain_failed_ranges': self.uncertain_ranges,
                'accounting': 'Artifact payload reservations; metadata and headers excluded'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class SignedRanges:
    """Resolve a short-lived storage URL using the authorized gh session."""
    def __init__(self, gh, repo, artifact_id, metadata, budget):
        self.gh, self.repo, self.artifact_id = gh, repo, str(artifact_id)
        self.size, self.budget = metadata['size_in_bytes'], budget
        self.url = None
        self.refresh_at = 0

    def resolve(self):
        token = subprocess.check_output([self.gh, 'auth', 'token', '--hostname', 'github.com'],
                                        text=True, timeout=30,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0).strip()
        request = Request(f'https://api.github.com/repos/{self.repo}/actions/artifacts/{self.artifact_id}/zip',
                          headers={'Authorization': 'Bearer ' + token,
                                   'Accept': 'application/vnd.github+json'})
        try:
            with build_opener(NoRedirect()).open(request, timeout=60):
                raise ValueError('Artifact API did not return a signed storage redirect')
        except HTTPError as exc:
            if exc.code != 302:
                raise RuntimeError('Artifact URL request failed with HTTP ' + str(exc.code)) from None
            self.url = exc.headers.get('Location', '')
        finally:
            del token
        parsed = urlsplit(self.url)
        host = (parsed.hostname or '').lower()
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443) or
                not (host.endswith('.blob.core.windows.net') or host.endswith('.actions.githubusercontent.com'))):
            raise ValueError('Unexpected signed artifact storage host')
        # Azure redirects currently expire after ten minutes. Long incremental
        # imports must renew before expiry, not wait for a storage 403. This is
        # a new metadata lookup, not a retry or duplicate payload request.
        from datetime import datetime, timezone
        from urllib.parse import parse_qs
        ttl=480.0
        expiry=parse_qs(parsed.query).get('se',[None])[0]
        if expiry:
            expires=datetime.fromisoformat(expiry.replace('Z','+00:00'))
            if expires.tzinfo is None:raise ValueError('Signed expiry lacks timezone')
            ttl=min(ttl,(expires-datetime.now(timezone.utc)).total_seconds()-60)
            if ttl<=0:
                self.url=None
                raise RuntimeError('Artifact storage redirect already expired or near expiry')
        self.refresh_at=time.monotonic()+ttl

    def __call__(self, start, stop):
        if self.url is None or time.monotonic()>=self.refresh_at:
            self.resolve()
        size = stop - start
        self.budget.reserve(size)
        request = Request(self.url, headers={'Range': f'bytes={start}-{stop - 1}', 'Accept-Encoding': 'identity'})
        # Do not follow another redirect or forward the GitHub credential.
        try:
            with build_opener(NoRedirect()).open(request, timeout=60) as response:
                expected = f'bytes {start}-{stop - 1}/{self.size}'
                if response.status != 206 or response.headers.get('Content-Range') != expected:
                    raise ValueError('Storage did not honor the exact artifact range')
                data = response.read(size)
                self.budget.downloaded += len(data)
                if len(data) != size:
                    raise RuntimeError('Artifact range response was truncated')
                return data
        except HTTPError as exc:
            # Only the next scheduler execution resolves a new URL; no hidden retry.
            self.url = None
            raise RuntimeError('Artifact storage request failed with HTTP ' + str(exc.code)) from None


class RangeFile(io.RawIOBase):
    """Seekable ZIP backed by immutable, individually verified cached ranges."""
    def __init__(self, directory, metadata, fetch, budget=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.metadata = {key: metadata.get(key) for key in ('id', 'size_in_bytes', 'digest')}
        self.size = self.metadata['size_in_bytes']
        if not isinstance(self.size, int) or not 0 < self.size <= 1024 * 1024 * 1024:
            raise ValueError('Artifact compressed size exceeds the transfer bound')
        self.index = self.directory / 'ranges.json'
        saved = json.loads(self.index.read_text()) if self.index.exists() else {'identity': self.metadata, 'ranges': []}
        if saved['identity'] != self.metadata:
            raise ValueError('Saved artifact identity changed; refuse range reuse')
        self.ranges = saved['ranges']
        self.fetch, self.budget, self.position = fetch, budget, 0
        self.checked = set()

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        value = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset if whence == 2 else -1
        if value < 0 or value > self.size:
            raise ValueError('Artifact seek outside bounds')
        self.position = value
        return value

    def read(self, size=-1):
        size = self.size - self.position if size < 0 else min(size, self.size - self.position)
        if size > 256 * 1024 * 1024:
            raise ValueError('Artifact range read exceeds the memory bound')
        stop, chunks = self.position + size, []
        while self.position < stop:
            cached = next((item for item in self.ranges if item['start'] <= self.position < item['stop']), None)
            if cached:
                path = self.directory / cached['file']
                if not re.fullmatch(r'[0-9]+-[0-9]+\.bin', cached['file']):
                    raise ValueError('Unsafe saved range filename')
                if cached['file'] not in self.checked:
                    if path.stat().st_size != cached['stop'] - cached['start'] or hashlib.sha256(path.read_bytes()).hexdigest() != cached['sha256']:
                        raise ValueError('Saved artifact range failed integrity validation')
                    self.checked.add(cached['file'])
                end = min(stop, cached['stop'])
                with path.open('rb') as stream:
                    stream.seek(self.position - cached['start'])
                    data = stream.read(end - self.position)
                if self.budget:
                    self.budget.cache_bytes += len(data)
            else:
                next_cached = min((item['start'] for item in self.ranges if item['start'] > self.position), default=stop)
                end = min(stop, next_cached, self.position + MAX_RANGE)
                try:
                    data = self.fetch(self.position, end)
                except TransferDeferred:
                    raise
                except Exception:
                    if self.budget:
                        self.budget.uncertain_ranges += 1
                    raise
                if len(data) != end - self.position:
                    raise RuntimeError('Range transport returned incomplete bytes')
                name = f'{self.position}-{end}.bin'
                path = self.directory / name
                temporary = path.with_suffix('.tmp')
                temporary.write_bytes(data)
                temporary.replace(path)
                self.ranges.append({'start': self.position, 'stop': end, 'file': name,
                                    'sha256': hashlib.sha256(data).hexdigest()})
                atomic_json(self.index, {'identity': self.metadata, 'ranges': self.ranges})
                self.checked.add(name)
            chunks.append(data)
            self.position = end
        return b''.join(chunks)


class CachedBlocks:
    """Amortize tiny ZIP member reads over bounded, verified cached blocks."""
    def __init__(self,directory,metadata,fetch,budget=None):
        self.reader=RangeFile(directory,metadata,fetch,budget)

    def __call__(self,start,stop):
        chunks=[]
        while start<stop:
            aligned=start//MAX_RANGE*MAX_RANGE
            end=min(aligned+MAX_RANGE,self.reader.size)
            self.reader.seek(aligned)
            block=self.reader.read(end-aligned)
            take=min(stop,end)
            chunks.append(block[start-aligned:take-aligned])
            start=take
        return b''.join(chunks)


class ArtifactZip:
    """Read individual members with ZIP CRC checks, retaining resumable ranges."""
    def __init__(self, reader):
        # Bound central-directory reads before ZipFile allocates them.
        reader.seek(max(0, reader.size - 65557))
        tail = reader.read()
        marker = tail.rfind(b'PK\x05\x06')
        if marker < 0 or len(tail) - marker < 22:
            raise ValueError('Artifact has no ZIP directory')
        import struct
        _, disk, central_disk, entries_disk, entries, directory_size, directory_offset, comment = struct.unpack_from('<4s4H2IH', tail, marker)
        if disk or central_disk or entries_disk != entries or len(tail) - marker != 22 + comment:
            raise ValueError('Unsupported ZIP directory structure')
        if directory_size == 0xffffffff or directory_offset == 0xffffffff or entries == 65535:
            # GitHub ZIP64 uses a fixed record immediately before its locator.
            absolute = reader.size - len(tail) + marker
            reader.seek(absolute - 20)
            locator = reader.read(20)
            if locator[:4] != b'PK\x06\x07':
                raise ValueError('Missing ZIP64 directory locator')
            zip64_disk, record_offset, total_disks = struct.unpack_from('<IQI', locator, 4)
            reader.seek(record_offset)
            record = reader.read(56)
            if record[:4] != b'PK\x06\x06' or zip64_disk or total_disks != 1:
                raise ValueError('Unsupported ZIP64 archive')
            entries, directory_size, directory_offset = struct.unpack_from('<QQQ', record, 32)
        if entries > 50000 or directory_size > MAX_DIRECTORY or directory_offset + directory_size > reader.size:
            raise ValueError('Artifact directory exceeds bounds')
        self.reader = reader
        self.archive = zipfile.ZipFile(reader)
        infos = [info for info in self.archive.infolist() if not info.is_dir()]
        if len(infos) > 50000 or sum(info.file_size for info in infos) > 2 * 1024 ** 3:
            raise ValueError('Expanded artifact exceeds bounds')
        from pathlib import PurePosixPath
        paths = set()
        for info in infos:
            name = info.filename
            path = PurePosixPath(name)
            if (not name or '\\' in name or ':' in name or path.is_absolute() or '..' in path.parts or
                    str(path) != name or name in paths or (info.external_attr >> 16) & 0o170000 == 0o120000 or
                    info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                raise ValueError('Unsafe, duplicate or unsupported artifact member')
            paths.add(name)
        self.members = {info.filename: info for info in infos}

    def read(self, name, maximum=256 * 1024 * 1024):
        info = self.members[name]
        if info.file_size > maximum:
            raise ValueError('Selected member exceeds its read bound')
        return self.archive.read(info)

    def close(self):
        self.archive.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

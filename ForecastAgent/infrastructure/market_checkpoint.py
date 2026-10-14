"""Lossless market checkpoints, independent of the frozen forecasting engine.

Control files stay directly readable. Each task is a deterministic XZ pack of
unique SHA-256 objects. Monitors restore only ledger and compatibility controls.
Legacy archives are migrated on GitHub, never on the user's network connection.
"""
import argparse
import hashlib
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
from urllib.request import Request, build_opener
import zipfile

SCHEMA = 'market-lossless-checkpoint-v1'
MAX_EXPANDED = 16 * 1024 ** 3
MAX_COMPRESSED = 2 * 1024 ** 3
MAX_FILE = 128 * 1024 ** 2
MAX_FILES = 50000
ZERO = hashlib.sha256(b'0').hexdigest()


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 ** 2), b''):
            result.update(block)
    return result.hexdigest()


def safe(name):
    path = PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or path.is_absolute() or
            '..' in path.parts or str(path) != name):
        raise ValueError('Unsafe checkpoint path')
    return name


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False), encoding='utf-8')


def selected(name, projection):
    return (not projection or not name.startswith('tasks/') or
            re.fullmatch(r'tasks/[0-9a-f]{64}/(?:collection-request.json|evidence-capture-v2/identity.json)', name))


def validate_index(files):
    if not isinstance(files, dict) or len(files) > MAX_FILES or 'state.json' not in files:
        raise ValueError('Missing or oversized checkpoint index')
    total = 0
    for name, row in files.items():
        safe(name)
        if (not isinstance(row, dict) or not re.fullmatch('[0-9a-f]{64}', row.get('sha256', '')) or
                type(row.get('size')) is not int or not 0 <= row['size'] <= MAX_FILE):
            raise ValueError('Invalid checkpoint object declaration')
        total += row['size']
    if total > MAX_EXPANDED:
        raise ValueError('Expanded checkpoint bound exceeded')
    return total


def publish(root, output):
    from ForecastAgent.market_repricing.common import verify
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('Checkpoint output overlaps source')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Checkpoint output must be empty')
    verify(root)
    files, groups = {}, {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink in checkpoint')
        if not path.is_file() or path.name.endswith('.tmp'):
            continue
        name = safe(path.relative_to(root).as_posix())
        files[name] = {'sha256': sha(path), 'size': path.stat().st_size}
        group = '/'.join(name.split('/')[:2]) if name.startswith('tasks/') and not selected(name, True) else None
        if group:
            groups.setdefault(group, []).append(name)
    total = validate_index(files)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {'schema': SCHEMA, 'files': files, 'packs': {}, 'original_bytes': total,
                'original_inventory_sha256': files['inventory.json']['sha256'],
                'lossless': True, 'budgets_reset': False,
                'control_names': [name for name in files if selected(name, True)]}
    for name in files:
        if selected(name, True):
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, target)
    for group, names in sorted(groups.items()):
        unique = {files[name]['sha256']: name for name in names}
        target = output / 'packs' / (group.split('/')[1] + '.tar.xz')
        target.parent.mkdir(exist_ok=True)
        with lzma.open(target, 'wb', preset=6) as compressed, tarfile.open(fileobj=compressed, mode='w|') as archive:
            for digest, name in sorted(unique.items()):
                info = tarfile.TarInfo('objects/' + digest)
                info.size, info.mtime, info.mode = files[name]['size'], 0, 0o444
                with (root / name).open('rb') as source:
                    archive.addfile(info, source)
        row = {'path': target.relative_to(output).as_posix(), 'sha256': sha(target),
               'size': target.stat().st_size, 'files': names,
               'objects': {digest: files[name]['size'] for digest, name in unique.items()}}
        manifest['packs'][group] = row
    manifest['published_bytes'] = sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
    if manifest['published_bytes'] > MAX_COMPRESSED:
        raise ValueError('Published checkpoint bound exceeded')
    write(output / 'state-pack-manifest.json', manifest)
    return {'schema': SCHEMA, 'files': len(files), 'task_packs': len(groups), 'original_bytes': total,
            'published_bytes': sum(p.stat().st_size for p in output.rglob('*') if p.is_file()),
            'unique_task_object_bytes': sum(sum(p['objects'].values()) for p in manifest['packs'].values()),
            'budget_reset': False}


def zip_members(archive):
    rows, total = {}, 0
    for info in archive.infolist():
        if info.is_dir():
            safe(info.filename.rstrip('/'))
            continue
        safe(info.filename)
        if info.filename in rows or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
            raise ValueError('Duplicate, encrypted or symlink checkpoint member')
        if info.file_size > MAX_FILE:
            raise ValueError('Checkpoint member exceeds file bound')
        rows[info.filename] = info
        total += info.file_size
    if len(rows) > MAX_FILES or total > MAX_EXPANDED:
        raise ValueError('Checkpoint ZIP exceeds bounds')
    return rows


def copy_member(archive, info, target, expected=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info) as source, target.open('wb') as dest:
        shutil.copyfileobj(source, dest, 1024 ** 2)
    if target.stat().st_size != info.file_size or expected and sha(target) != expected:
        raise ValueError('Checkpoint original integrity failure')


def unpack_pack(path, row, files, output, projection=False):
    if path.stat().st_size != row['size'] or sha(path) != row['sha256']:
        raise ValueError('Task pack hash mismatch')
    expected, seen = row['objects'], set()
    if not isinstance(expected, dict) or sum(expected.values()) > MAX_EXPANDED:
        raise ValueError('Invalid pack expansion declaration')
    destinations = {}
    for name in row['files']:
        safe(name)
        item = files[name]
        if expected.get(item['sha256']) != item['size']:
            raise ValueError('Pack original mapping differs')
        if selected(name, projection):
            destinations.setdefault(item['sha256'], []).append(output / name)
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        with lzma.open(path, 'rb') as decoded, tarfile.open(fileobj=decoded, mode='r|') as archive:
            for info in archive:
                if not info.isreg() or not re.fullmatch('objects/[0-9a-f]{64}', info.name):
                    raise ValueError('Unexpected task pack member')
                digest = info.name.split('/')[1]
                if digest in seen or expected.get(digest) != info.size or not 0 <= info.size <= MAX_FILE:
                    raise ValueError('Task pack object differs')
                seen.add(digest)
                target = Path(scratch) / digest
                with archive.extractfile(info) as source, target.open('wb') as dest:
                    shutil.copyfileobj(source, dest, 1024 ** 2)
                if target.stat().st_size != info.size or sha(target) != digest:
                    raise ValueError('Decoded task object differs')
                for dest in destinations.get(digest, []):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    # Copies, never hardlinks: mutable native paths must remain independent.
                    shutil.copyfile(target, dest)
                target.unlink()
    if seen != set(expected):
        raise ValueError('Task pack objects are incomplete')


def extract_file(source, root, projection=False):
    """Atomic restoration: failed integrity checks cannot publish partial state."""
    from ForecastAgent.market_repricing.common import read, verify, seal
    from ForecastAgent.market_repricing.github_state import recover_omitted_mutexes
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError('Restore target must be empty')
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root.parent) as temporary:
        staging = Path(temporary) / 'state'
        staging.mkdir()
        with zipfile.ZipFile(source) as archive:
            members = zip_members(archive)
            packed = 'state-pack-manifest.json' in members
            if packed:
                manifest = json.loads(archive.read(members['state-pack-manifest.json']))
                if manifest.get('schema') != SCHEMA:
                    raise ValueError('Unknown checkpoint codec')
                files = manifest['files']
                validate_index(files)
                root_names = set(manifest['control_names'])
                if root_names != {name for name in files if selected(name, True)}:
                    raise ValueError('Checkpoint projection controls are incomplete')
                packs = manifest['packs']
                assigned = [name for row in packs.values() for name in row['files']]
                if len(set(assigned)) != len(assigned) or set(assigned) != set(files) - root_names:
                    raise ValueError('Pack partition is incomplete or duplicated')
                if any(not name.startswith(group + '/') for group, row in packs.items() for name in row['files']):
                    raise ValueError('Task pack crosses its partition')
                wanted = root_names | {safe(row['path']) for row in packs.values()} | {'state-pack-manifest.json'}
                if set(members) != wanted or len(wanted) != len(root_names) + len(packs) + 1:
                    raise ValueError('Checkpoint transport member set differs')
                for name in root_names:
                    if members[name].file_size != files[name]['size']:
                        raise ValueError('Control original size differs')
                    copy_member(archive, members[name], staging / name, files[name]['sha256'])
                for group, row in packs.items():
                    if projection and not any(selected(name, True) for name in row['files']):
                        continue
                    packed_path = Path(temporary) / 'task.tar.xz'
                    copy_member(archive, members[row['path']], packed_path, row['sha256'])
                    unpack_pack(packed_path, row, files, staging, projection)
                    packed_path.unlink()
            else:
                if 'inventory.json' not in members:
                    raise ValueError('Legacy checkpoint has no original inventory')
                inventory = json.loads(archive.read(members['inventory.json']))
                declared = inventory['files']
                for name, expected in declared.items():
                    safe(name)
                    if name not in members and not (expected == ZERO and re.fullmatch(
                            r'tasks/[0-9a-f]{64}/development-intelligence/(?:[^./][^/]*/)*\.lock\.recovery', name)):
                        raise ValueError('Legacy checkpoint lost original bytes')
                if any(name not in declared and name != 'inventory.json' and not name.endswith('.tmp') for name in members):
                    raise ValueError('Legacy checkpoint has undeclared original bytes')
                for name, info in members.items():
                    if selected(name, projection) and not name.endswith('.tmp'):
                        copy_member(archive, info, staging / name, declared.get(name))
                if not projection:
                    recover_omitted_mutexes(staging)
            if projection:
                shutil.copyfile(staging / 'inventory.json', staging / 'projection-original-inventory.json')
                write(staging / 'projection-receipt.json', {'schema': SCHEMA, 'scope': 'ledger_and_compatibility_controls',
                    'body_bytes_restored': False, 'full_body_hash_verification': False,
                    'original_inventory_sha256': sha(staging / 'inventory.json')})
                seal(staging)
            verify(staging)
            # No reset or JSON regeneration of any original ledger or provider file.
            report = {'packed_source': packed, 'projection': projection, 'files_verified': verify(staging),
                      'state_sha256': sha(staging / 'state.json'), 'budget_reset': False}
        if root.exists():
            root.rmdir()
        os.replace(staging, root)
    return report


def restore(role, root, branch, exact_run=None, projection=False):
    from ForecastAgent.market_repricing.github_state import find, ArtifactRedirect
    artifact = find(role, branch, exact_run)
    if artifact is None:
        return {'bootstrap': True, 'role': role}
    if artifact['size_in_bytes'] > MAX_COMPRESSED:
        raise ValueError('Compressed checkpoint metadata exceeds bound')
    request = Request('https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] +
        '/actions/artifacts/' + str(artifact['id']) + '/zip', headers={
        'Authorization': 'Bearer ' + os.environ['GH_STATE_TOKEN'], 'Accept': 'application/vnd.github+json'})
    # Disk streaming avoids simultaneously holding a gigabyte ZIP and decoded state.
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / 'checkpoint.zip'
        total = 0
        with build_opener(ArtifactRedirect()).open(request, timeout=120) as response, path.open('wb') as dest:
            while block := response.read(1024 ** 2):
                total += len(block)
                if total > MAX_COMPRESSED:
                    raise ValueError('Compressed checkpoint exceeds bound')
                dest.write(block)
        if total != artifact['size_in_bytes'] or artifact.get('digest') and artifact['digest'] != 'sha256:' + sha(path):
            raise ValueError('Actions checkpoint digest differs')
        result = extract_file(path, root, projection)
    return result | {'bootstrap': False, 'artifact_id': artifact['id'], 'role': role,
                     'cloud_download_bytes': total, 'local_download_bytes': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['restore', 'publish'])
    parser.add_argument('--root', required=True)
    parser.add_argument('--output')
    parser.add_argument('--role', choices=['monitor', 'worker'])
    parser.add_argument('--branch', default='main')
    parser.add_argument('--run-id')
    parser.add_argument('--projection', action='store_true')
    args = parser.parse_args()
    if args.operation == 'publish':
        if not args.output:
            parser.error('publish requires --output')
        result = publish(args.root, args.output)
    else:
        if not args.role:
            parser.error('restore requires --role')
        result = restore(args.role, args.root, args.branch, args.run_id, args.projection)
    print(json.dumps(result))


if __name__ == '__main__':
    main()

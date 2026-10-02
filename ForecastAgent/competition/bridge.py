"""Freeze collector outputs for analysis without creating new retrieval budgets."""
import copy
import hashlib
import shutil
import zipfile
from pathlib import Path

from .queue import digest, load, save, utc


def freeze(task, collector_root, destination, now=None):
    destination = Path(destination)
    ident = task['id']
    original = Path(collector_root) / 'retrieval' / ident / 'bundle.json'
    if not original.exists():
        return None
    raw = original.read_bytes()
    bundle = load(original)
    request = bundle['request']
    question = task['question']
    expected = {'question': question['title'], 'resolution_criteria': question['resolution_criteria'],
                'fine_print': question['fine_print']}
    if any((request.get(key) or '') != value for key, value in expected.items()):
        raise ValueError('Collector question/rule identity mismatch')
    if request.get('mode') != 'live':
        raise ValueError('Historical collection cannot become a live competition input')
    if not bundle.get('result') or bundle['result'].get('incomplete'):
        return None
    deadline = utc(question['deadline_utc'])
    current = utc(now)
    readable = 0
    for page in bundle.get('pages', {}).values():
        body = page.get('content', '')
        if not isinstance(body, str) or not body.strip():
            continue
        readable += 1
        if page.get('content_sha256') and hashlib.sha256(body.encode()).hexdigest() != page['content_sha256']:
            raise ValueError('Collector body hash mismatch')
        captured = page.get('retrieved_at_utc')
        if not captured or utc(captured) >= deadline or utc(captured) > current:
            raise ValueError('Readable source missing valid pre-deadline capture timestamp')
    if not readable:
        raise ValueError('No readable collection bodies; preserve the acquisition gap')
    identity = {'schema': 'live-evidence-bridge-v1', 'question_id': ident,
                'rule_sha256': question['rule_sha256'], 'original_bundle_sha256': hashlib.sha256(raw).hexdigest(),
                'original_canonical_sha256': digest(bundle), 'source': 'existing live collector',
                'no_new_retrieval_budget': True, 'historical_analysis_prompt': True}
    marker = destination / 'bridge.json'
    if marker.exists():
        recorded = dict(load(marker))
        recorded.pop('adopted_bundle_sha256')
        if recorded != identity:
            raise ValueError('Frozen collector identity changed; cannot restart analysis budgets')
        validate_frozen(destination)
        return destination
    # Analysis artifacts without a committed bridge cannot safely be adopted.
    if (destination.parent / 'analysis').exists():
        raise ValueError('Analysis exists without frozen bridge identity')
    target = destination / 'tasks' / ident
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(original.parent, target, dirs_exist_ok=True)
    save(destination / 'original-bundle.json', bundle)
    adopted = copy.deepcopy(bundle)
    adopted['request'].update(id=ident, close_time=question['deadline_utc'],
                              as_of_utc=current.isoformat())
    save(target / 'bundle.json', adopted)
    save(destination / 'campaign.json', {'schema': 'competition-analysis-input-v1',
        'tasks': {ident: {'status': 'closed_with_gaps' if bundle['result'].get('gaps') else 'acquired'}},
        'bridge': identity})
    identity['adopted_bundle_sha256'] = digest(adopted)
    # Compare identity without adoption metadata on later calls.
    save(marker, identity)
    validate_frozen(destination)
    return destination


def validate_frozen(destination):
    destination = Path(destination)
    marker = load(destination / 'bridge.json')
    bundle = load(destination / 'tasks' / marker['question_id'] / 'bundle.json')
    if digest(bundle) != marker['adopted_bundle_sha256']:
        raise ValueError('Frozen adopted bundle hash mismatch')
    original = load(destination / 'original-bundle.json')
    if digest(original) != marker['original_canonical_sha256']:
        raise ValueError('Frozen original bundle hash mismatch')
    campaign = load(destination / 'campaign.json')
    recorded = dict(marker)
    recorded.pop('adopted_bundle_sha256')
    if campaign.get('bridge') != recorded:
        raise ValueError('Frozen campaign bridge identity mismatch')
    return marker


def archive_input(destination):
    """Create the supplement input from the committed immutable bridge."""
    destination = Path(destination)
    validate_frozen(destination)
    archive = destination.parent / 'supplement-input.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in sorted(destination.rglob('*')):
            if path.is_file():
                output.write(path, path.relative_to(destination).as_posix())
    return archive

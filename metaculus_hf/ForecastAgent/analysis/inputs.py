"""Resolve immutable original and supplemental evidence for analysis and audit."""
from pathlib import Path

from ForecastAgent.analysis.pilot import digest, load
from ForecastAgent.supplement.stage import analysis_overlay


def supplement_identity(root):
    if root is None:
        return None
    root = Path(root)
    names = ['campaign-manifest.json', 'campaign-state.json'] if (root / 'campaign-state.json').exists() else ['manifest.json']
    return {name: digest(load(root / name)) for name in names}


def resolve_bundle(bundle, ident, supplement_root=None):
    if supplement_root is None:
        return bundle
    root = Path(supplement_root)
    if (root / 'campaign-state.json').exists():
        row = load(root / 'campaign-state.json')['tasks'][ident]
        if row['status'] != 'assessed':
            raise ValueError('Supplement task is not assessed')
        candidate = (root / 'batches' / row['batch']).resolve()
        if not candidate.is_relative_to(root.resolve()):
            raise ValueError('Invalid supplement batch path')
        root = candidate
    overlay = analysis_overlay(bundle, root, ident)
    child = load(root / 'tasks' / ident / 'supplement.json')
    overlay['supplement_lineage'] = {**overlay['supplement_lineage'],
                                   'remaining_gaps': child['remaining_gaps'],
                                   'sidecar_sha256': digest(child)}
    return overlay


def source_metadata(page):
    keys = ('retrieved_at_utc', 'published_at', 'published_at_utc', 'temporal_status',
            'capture_method', 'raw_kind', 'content_truncated', 'body_diagnostics',
            'supplement_provenance')
    return {key: page[key] for key in keys if key in page}

"""Explicit development adapter; never impersonates a frozen release package."""
import copy
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path

from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.channels.contracts import FIELD, POLICY, code_identity
from ForecastAgent.acquisition import pipeline

DEFAULT_MODEL = 'nvidia/nemotron-3-super-120b-a12b:free'


@contextmanager
def model_route(model):
    """Configuration chooses the model; tool and research prompts remain neutral."""
    previous = {k: os.environ.get(k) for k in ('FORECAST_MODEL', 'FORECAST_MODEL_FALLBACK_SUPER')}
    os.environ['FORECAST_MODEL'] = model
    os.environ['FORECAST_MODEL_FALLBACK_SUPER'] = '0'
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def collect(request, directory):
    """Use the unchanged collection/supplement runner with candidate identity.

The release verifier and manifests stay strict. An incomplete native collection
remains resumable and is not converted into a complete package by this adapter.
"""
    if request.get(FIELD) != POLICY:
        raise ValueError('Explicit native channel development policy required')
    request = copy.deepcopy(request)
    code = code_identity()
    if 'capability_code_identity' in request and request['capability_code_identity'] != code:
        raise ValueError('Candidate capability code identity changed')
    request['capability_code_identity'] = code
    request.update(acquisition_strategy='intelligent_materials_v3', drain_unseen_reads_before_stall=True,
        source_reading_policy='crawl4ai_v1', recover_sources_before_stall=True, exa_search_policy='required',
        source_recovery_policy_sha256=hashlib.sha256((Path(__file__).parents[1] / 'runtime/source_frontier.py')
            .read_bytes().replace(b'\r\n', b'\n')).hexdigest())
    directory = Path(directory)
    model = request.get('collection_model', DEFAULT_MODEL)
    if not isinstance(model, str) or not model.strip():
        raise ValueError('Collection model must be a nonempty configured model ID')
    with model_route(model):
        report = pipeline.run(request, directory, supplement_network=True)
    marker = {'schema': 'native_channels_development_collection_v1', 'status': report.get('state'),
        'development_only': True, 'release_version_claimed': None,
        'budget_reset': False, 'analysis_started': False, 'submitted': False}
    if report.get('state') != 'complete':
        marker.update(status='collection_incomplete', resumable=True, collector_report=report)
        save(directory / 'development-adapter.json', marker)
        return marker
    package = load(directory / 'package.json')
    if str(package['request']['id']) != str(request['id']):
        raise ValueError('Candidate package question identity mismatch')
    marker['status'] = 'complete'
    save(directory / 'development-adapter.json', marker)
    return {**marker, 'package': package}

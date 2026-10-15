"""Exercise the actual pinned market callable with offline tool/model boundaries."""
import argparse
import base64
import copy
import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import types
import urllib.error
import urllib.request
from unittest.mock import patch

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('shared_launcher', HERE/'unified_acquisition.py')
launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)


def validate_release_input(path, release):
    code = '''import json,sys,urllib.request
def forbidden(*a, **kw):raise AssertionError('Unexpected provider call')
urllib.request.urlopen=forbidden
from ForecastAgent.releases import context_decisions as context
from ForecastAgent.competition.mercury import distribution_spec
bundle=json.load(open(sys.argv[1]))
kind=bundle['request']['question_type']
registry=context.chain.questions() if kind=='binary' else context.typed.questions(distribution_spec(bundle['request']))
state,audit=context.select_first(bundle,registry)
assert state['evidence'], 'Readable original did not reach unchanged decision context'
assert not context.audit_spans(bundle,state), 'Decision evidence is not bound to original spans'
print(json.dumps({'unchanged_release_evidence_selection':True,'selected_spans':len(state['evidence']),'mode':audit['mode']}))
'''
    env = dict(os.environ, PYTHONPATH=str(Path(release).resolve()))
    return json.loads(subprocess.check_output([sys.executable, '-c', code, str(Path(path).resolve())],
        cwd=str(Path(release).resolve()), env=env, text=True))


def main(core, engine, expected, output, release=None):
    identity = launcher.verify(core, engine, expected)
    from ForecastAgent.market_repricing.runtime import activate
    activate(engine)
    os.environ['MARKET_PIPELINE_SHA'] = expected
    from ForecastAgent.intelligence import pipeline
    from ForecastAgent.research_loop import post_supplement
    from ForecastAgent.market_repricing import worker
    from ForecastAgent.market_repricing.common import policy
    from ForecastAgent.market_repricing.budgets import ProviderCap
    cases, calls = [], []
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        def collect(request, directory, **kwargs):
            calls.append({'request': copy.deepcopy(request), 'kwargs': kwargs})
            body = ('Official observation on 2026-10-15: the reported value was 42 units. '
                    'This is the exact original document and it describes the specified reporting period. ')*4
            raw = ('<article>'+body+'</article>').encode()
            bundle = {'request': copy.deepcopy(request), 'mode': 'live',
                'pages': {'https://agency.example/report': {'url': 'https://agency.example/report',
                    'content': body, 'content_type': 'text/html', 'retrieved_at_utc': '2026-10-15T00:00:00Z',
                    'raw_response_base64': base64.b64encode(raw).decode(),
                    'sha256': __import__('hashlib').sha256(raw).hexdigest()}},
                'fetch_attempts': [], 'source_leads': {}, 'searches': [], 'exa_searches': [],
                'evidence': [], 'excerpts': [], 'result': {'status': 'collected', 'incomplete': False}}
            target = Path(directory)/'retrieval/development-channels/package.json'
            launcher.save(target, bundle)
            return {'report': {'status': 'offline_boundary_fixture'}}
        def no_network(*args, **kwargs):
            raise AssertionError('Unexpected actual HTTP call in offline integration')
        with patch.object(pipeline, 'collect', collect), patch.object(post_supplement, 'run',
                lambda bundle, *a, **kw: (bundle, {'status': 'offline_review', 'model_calls': 0})), \
                patch.object(urllib.request, 'urlopen', no_network):
            for index, kind in enumerate(['binary', 'multiple_choice', 'numeric', 'date', 'discrete']):
                request = {'id': str(998000+index), 'question': 'Exact '+kind+' original contract?',
                    'question_type': kind, 'resolution_criteria': 'Official observation only.',
                    'fine_print': 'Keep the original exception.', 'mode': 'live',
                    'as_of_utc': '2026-10-15T00:00:00Z'}
                if kind == 'multiple_choice': request['options'] = ['Exact A', 'Exact B']
                if kind in {'numeric', 'date', 'discrete'}:
                    request.update(scaling={'range_min': 0, 'range_max': 100, 'zero_point': None},
                        inbound_outcome_count=10, open_lower_bound=True, open_upper_bound=False, unit='original unit')
                directory = root/kind
                first = launcher.acquire(request, directory, root/'budget', identity)
                before = len(calls)
                second = launcher.acquire(request, directory, root/'budget', identity)
                assert first == second and len(calls) == before
                assert calls[-1]['kwargs'] == {'clock_utc': request['as_of_utc'],
                    'recover_data': True, 'research_map': True, 'channel_tools': True}
                assert calls[-1]['request']['market_search_profile']['caps'] == {'basic': 2, 'advanced': 1, 'total': 3}
                assert first['request']['question_type'] == kind and len(first['pages']) == 1
                assert (directory/'evidence-capture-v2/packets/first-packet.json').exists()
                cases.append({'kind': kind, 'actual_market_acquire_and_repair_used': True,
                    'raw_byte_packet_preserved': True, 'resume_reused': True, 'network_calls': 0})
                if release:
                    cases[-1]['release_analysis_input'] = validate_release_input(directory/'package.json', release)

        # Each primary and backup transmission must own a separate market-native
        # physical reservation, including calls from imported provider aliases.
        physical_calls = []
        def transport(request, *args, **kwargs):
            physical_calls.append(request)
            auth = request.get_header('Authorization', '')
            if auth.endswith('primary-fixture'):
                body = b'{"error":{"code":402,"message":"Insufficient credits"}}'
                raise urllib.error.HTTPError(request.full_url, 402, 'Fixture', {}, io.BytesIO(body))
            response = io.BytesIO(b'{}'); response.status = 200
            return response
        env = {'OPENROUTER_API_KEY': 'primary-fixture', 'OPENROUTER_API_KEY2': 'backup-fixture',
               'TAVILY_API_KEY': '', 'TAVILY_API_KEY2': '', 'EXA_API_KEY': '', 'EXA_API_KEY2': ''}
        ledger = {'provider_reservations': []}
        with patch.dict(os.environ, env), patch.object(urllib.request, 'urlopen', transport):
            with launcher.physical_transport(root/'physical', ledger, 'fixture', policy()):
                urllib.request.urlopen(urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',
                    data=b'{}', headers={'Authorization': 'Bearer primary-fixture'}, method='POST'))
        assert len(physical_calls) == len(ledger['provider_reservations']) == 2
        limited = {'provider_reservations': []}; physical_calls.clear()
        with patch.dict(os.environ, env), patch.object(urllib.request, 'urlopen', transport):
            try:
                with launcher.physical_transport(root/'limited', limited, 'fixture',
                        policy() | {'maximum_worker_model_attempts_per_task': 1}):
                    urllib.request.urlopen(urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',
                        data=b'{}', headers={'Authorization': 'Bearer primary-fixture'}, method='POST'))
            except Exception:
                pass
            else:
                raise AssertionError('The backup must stop at the cumulative physical cap')
        assert len(physical_calls) == len(limited['provider_reservations']) == 1
    report = {'schema': 'unified-selected-engine-integration-v1', 'core': identity,
        'typed_cases': cases, 'physical_backup_counted_separately': True,
        'physical_cap_blocks_backup_before_transmission': True, 'actual_provider_calls': 0,
        'synthetic_transport_calls': 3, 'forecasts_submitted': False, 'budgets_reset': False}
    launcher.save(output, report); print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', required=True)
    parser.add_argument('--engine', required=True)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--release')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(); main(args.core, args.engine, args.expected, args.output, args.release)

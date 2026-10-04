"""Frozen V2/V3 paired material review with identical source packets."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path

from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.experiments.field_contract_round import tool_schema
from ForecastAgent.experiments.material_contract_round import MODEL
from ForecastAgent.supplement.field_contract import evaluate as evaluate_v3, span_catalog
from ForecastAgent.supplement.material_contract import evaluate as evaluate_v2, ROLES, RELATIONS

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name('MATERIAL_FIELD_PAIRED_UNSEEN10.json')


def frozen_prompt(path, prefix):
    """Read the unchanged literal prompt without maintaining a drifting copy."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    candidates = [n.value.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                  and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
                  and n.value.value.startswith(prefix)]
    if len(candidates) != 1:
        raise ValueError('frozen_prompt_not_unique')
    return candidates[0]


def validate_freeze(data):
    for filename, expected in data['frozen_git_blob_sha256'].items():
        raw = (ROOT / filename).read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('frozen_implementation_changed:' + filename)


def v2_tool():
    from ForecastAgent.supplement.research_loop import schema
    return schema('observe_material', {
        'document_role': {'type': 'string', 'enum': list(ROLES)},
        'fit_axes': {'type': 'object', 'properties': {a: {'type': 'boolean'} for a in
            ('entity', 'material_type', 'metric', 'period')},
            'required': ['entity', 'material_type', 'metric', 'period'], 'additionalProperties': False},
        'evidence_relation': {'type': 'string', 'enum': list(RELATIONS)},
        'quote': {'type': 'string', 'maxLength': 1200},
        'reason': {'type': 'string', 'maxLength': 240}},
        ['document_role', 'fit_axes', 'evidence_relation', 'quote', 'reason'])


def run(output, key, manifest=MANIFEST):
    from ForecastAgent.providers.model import ask_model
    from ForecastAgent.supplement.research_loop import decode
    data = load(manifest)
    validate_freeze(data)
    if os.environ.get('FORECAST_MODEL') != MODEL or os.environ.get('FORECAST_MODEL_FALLBACK_SUPER') != '0':
        raise ValueError('fixed_super_without_fallback_required')
    output = Path(output)
    if output.exists():
        raise ValueError('existing_journal_no_implicit_reset')
    output.mkdir(parents=True)
    prompts = {
        'v2': frozen_prompt(ROOT / 'ForecastAgent/experiments/material_contract_round.py',
                            'Assess document material fit'),
        'v3': frozen_prompt(ROOT / 'ForecastAgent/experiments/field_contract_round.py',
                            'Review saved material only.')}
    identity = {'schema': 'material-pair-unseen-v1', 'manifest_sha256': hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
        'model': MODEL, 'limits': data['per_arm_limits'], 'global_http_limit': data['global_http_limit'],
        'frozen_code': data['frozen_git_blob_sha256'],
        'prompt_sha256': {a: hashlib.sha256(p.encode()).hexdigest() for a, p in prompts.items()},
        'scope': data['scope']}
    save(output / 'identity.json', identity)
    state = {'decisions': [], 'attempts': []}
    save(output / 'state.json', state)
    active_arm = None

    def observer(event, record, token=None):
        if event == 'reserve':
            arm_attempts = sum(a['arm'] == active_arm for a in state['attempts'])
            if arm_attempts >= data['per_arm_limits']['http'] or len(state['attempts']) >= data['global_http_limit']:
                raise RuntimeError('paired_http_limit_reached')
            if record['request']['model'] != MODEL:
                raise RuntimeError('paired_model_changed')
            token = len(state['attempts'])
            state['attempts'].append({'arm': active_arm, 'status': 'reserved'})
            save(output / 'state.json', state)
        filename = f'provider-{token + 1:04d}.json'
        save(output / filename, json.loads(json.dumps(record).replace(key, '[REDACTED]')))
        state['attempts'][token] = {'arm': active_arm, 'status': record['status'], 'file': filename,
                                   'usage': record.get('response', {}).get('usage')}
        save(output / 'state.json', state)
        return token

    for index, case in enumerate(data['cases']):
        packet = {k: case[k] for k in ('contract', 'source', 'legacy_need')}
        packet.update(sample_id=f'sample-{index + 1:03d}', span_catalog=span_catalog(case['source']))
        user_text = json.dumps(packet)
        for arm in (['v2', 'v3'] if index % 2 == 0 else ['v3', 'v2']):
            active_arm = arm
            if sum(d['arm'] == arm for d in state['decisions']) >= data['per_arm_limits']['logical']:
                raise RuntimeError('paired_logical_limit_reached')
            tool = v2_tool() if arm == 'v2' else tool_schema(case, indexed=True)
            name = tool['function']['name']
            row = {'case_id': case['id'], 'arm': arm, 'status': 'reserved', 'input_sha256': digest(packet),
                   'attempts_before': len(state['attempts'])}
            state['decisions'].append(row)
            save(output / 'state.json', state)
            try:
                try:
                    message = ask_model([{'role': 'system', 'content': prompts[arm]},
                        {'role': 'user', 'content': user_text}], key, tools=[tool], forced_tool=name,
                        observer=observer, max_output_tokens=data['per_arm_limits']['output_tokens'][arm],
                        reasoning={'max_tokens': data['per_arm_limits']['reasoning_tokens']})
                except Exception:
                    state['blocked'] = True
                    raise
                last = load(output / state['attempts'][-1]['file'])
                if (last.get('response', {}).get('choices') or [{}])[0].get('finish_reason') == 'length':
                    raise ValueError('output_truncated')
                observed = decode(message, name)
                judgment = (evaluate_v2 if arm == 'v2' else evaluate_v3)(case['contract'], case['source'], observed)
                row.update(status='received', observation=observed, judgment=judgment,
                           expected=case['expected'], correct=judgment['status'] == case['expected'])
            except Exception as exc:
                row.update(status='failed', error=str(exc)[:240])
                if not isinstance(exc, ValueError):
                    state['blocked'] = True
            finally:
                row['attempts_after'] = len(state['attempts'])
                save(output / 'state.json', state)
            if state.get('blocked'):
                break
        if state.get('blocked'):
            break
    report = {'schema': 'material-pair-unseen-result-v1', 'identity': identity, 'results': state['decisions'],
        'actual_http_attempts': len(state['attempts']),
        'known_tokens': sum((a.get('usage') or {}).get('total_tokens', 0) for a in state['attempts']),
        'unknown_usage_attempts': sum(not a.get('usage') for a in state['attempts']),
        'search_calls': 0, 'fetch_calls': 0, 'forecast_submissions': 0}
    save(output / 'result.json', report)
    # Hide version names and native response shape for a separate semantic review.
    blind = []
    mapping = []
    for row in state['decisions']:
        swap = hashlib.sha256(('unseen-review-20261004:' + row['case_id']).encode()).digest()[0] % 2
        alias = ('A' if row['arm'] == 'v2' else 'B') if not swap else ('B' if row['arm'] == 'v2' else 'A')
        judgment = row.get('judgment', {})
        observation = row.get('observation', {})
        blind.append({'case_id': row['case_id'], 'alias': alias, 'status': judgment.get('status', 'failed'),
                      'reason': observation.get('reason', row.get('error')),
                      'issues': judgment.get('issues', []),
                      'spans': judgment.get('quote_binding', {}).get('spans', [])})
        mapping.append({'case_id': row['case_id'], 'alias': alias, 'arm': row['arm']})
    save(output / 'blinded-review.json', sorted(blind, key=lambda x: (x['case_id'], x['alias'])))
    save(output / 'blinding-map.json', mapping)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = run(args.output, os.environ['OPENROUTER_API_KEY'])
    print(json.dumps({k: v for k, v in result.items() if k != 'results'}))

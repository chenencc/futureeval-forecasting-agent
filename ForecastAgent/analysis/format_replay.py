"""Offline typed-output acceptance on historical metadata, never real forecasts."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.distributions import POLICY, grid, payload, range_metadata, validate_cdf
from ForecastAgent.analysis.range_forecast import LEVELS
from ForecastAgent.competition.queue import digest, load, save

DEFAULT_METADATA = Path(__file__).resolve().parents[1] / 'fixtures/nonbinary-format-2026.json'
WARNING = 'FORMAT ONLY: historical metadata plus synthetic provider outputs; no factual analysis or accuracy measurement.'


def run(metadata_path, output):
    output = Path(output)
    dataset = load(metadata_path)
    cases = [{**row, 'fixture_kind': 'historical_metadata'} for row in dataset['questions']]
    for ident, kind, scale in (
        ('900000001', 'date', {'range_min': 1767225600., 'range_max': 1798761600., 'zero_point': None}),
        ('900000002', 'numeric', {'range_min': 1., 'range_max': 10000., 'zero_point': 0.}),
    ):
        cases.append({'id': ident, 'title': f'Synthetic {kind} format control', 'type': kind,
            'scaling': scale, 'inbound_outcome_count': 200, 'open_lower_bound': False,
            'open_upper_bound': False, 'fixture_kind': 'synthetic_control'})
    rows = []
    with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline-format-fixture',
            'FORECAST_MODEL': 'nvidia/nemotron-3-ultra-550b-a55b:free'}):
        for question in cases:
            ident = str(question['id'])
            folder = output / 'tasks' / ident
            # Source fields are deliberately metadata only. Resolution values,
            # community distributions and labels are never loaded into a bundle.
            request = {k: question[k] for k in ('id', 'type', 'options', 'scaling',
                'inbound_outcome_count', 'open_lower_bound', 'open_upper_bound', 'unit') if k in question}
            request.update(id=ident, question_type=question['type'], question=question['title'],
                mode='format_only', resolution_criteria='', background=WARNING)
            source = 'Historical metadata title: ' + question['title']
            bundle = {'request': request, 'pages': {'offline://metadata': {'content': source}},
                'format_only': True, 'no_resolution_values': True}
            save(folder / 'bundle.json', bundle)
            qualitative = {'rule_decomposition': WARNING, 'base_rate': 'Not estimated',
                'contradictions': 'Not evaluated', 'source_independence': 'Metadata only',
                'facts': [{'source_id': 'S1', 'quote': source, 'claim': 'Metadata identity only'}],
                'gaps': ['No real evidence or live provider calls in this format test']}
            if question['type'] == 'multiple_choice':
                from ForecastAgent.analysis.categorical import run as analyze
                options = question['options']
                report = {**qualitative, 'option_analysis': [{'option': o, 'case_for': WARNING,
                    'case_against': WARNING} for o in options],
                    'probabilities': {o: float(i == 0) for i, o in enumerate(options)}}
                name = 'record_categorical'
            else:
                from ForecastAgent.analysis.range_forecast import run as analyze
                meta = range_metadata(request)
                locations = grid(meta)
                values = [locations[round(p * meta['inbound_outcome_count'])] for p in LEVELS]
                if meta['type'] == 'date':
                    values = [datetime.fromtimestamp(v, timezone.utc).isoformat() for v in values]
                report = {**qualitative, 'distribution_rationale': WARNING,
                    'quantiles': [{'probability': p, 'value': v} for p, v in zip(LEVELS, values)],
                    'below_lower_bound': 0., 'above_upper_bound': 0.}
                name = 'record_range'
            def ask(messages, key, **kwargs):
                record = {'request': {'model': kwargs['model_route'].model()},
                    'endpoint': 'offline://format-fixture', 'real_http': False, 'status': 'reserved'}
                token = kwargs['observer']('reserve', record)
                message = {'tool_calls': [{'function': {'name': name, 'arguments': json.dumps(report)}}]}
                record.update(status='received', response={'choices': [{'message': message}]})
                kwargs['observer']('complete', record, token)
                return message
            def decision(state, questions, key, observer):
                record = {'request': {'model': 'inception/mercury-decide:free'},
                    'endpoint': 'offline://format-fixture', 'real_http': False, 'status': 'reserved'}
                token = observer('reserve', record)
                if question['type'] == 'multiple_choice':
                    answer = {'type': 'choice', 'choice': 'option_0', 'confidence': 1.,
                        'probabilities': {f'option_{i}': float(i == 0) for i in range(len(options))}}
                    answers = {'event_outcome': answer}
                else:
                    answers = {k: {'type': 'noul', 'noul': int(k.split('_')[1]) / meta['inbound_outcome_count']} for k in questions}
                response = {'model': 'inception/mercury-decide:free', 'answers': answers}
                record.update(status='received', response=response)
                observer('complete', record, token)
                return response
            result = analyze(folder / 'bundle.json', folder / 'analysis', ask=ask, decision=decision)
            def forbidden(*args, **kwargs):
                raise AssertionError('A completed offline replay attempted a new provider call')
            resumed = analyze(folder / 'bundle.json', folder / 'analysis', ask=forbidden, decision=forbidden)
            if digest(result) != digest(resumed):
                raise ValueError('Replay changed typed result')
            candidate = payload(request, result['payload_preview'])
            save(folder / 'payload-preview.json', candidate)
            row = {'question_id': ident, 'type': question['type'], 'fixture_kind': question['fixture_kind'],
                'source_url': question.get('source_url'), 'status': result['status'],
                'payload_sha256': digest(candidate), 'replay_verified': True, 'format_only': True}
            if question['type'] == 'multiple_choice':
                values = candidate['probability_yes_per_category'].values()
                row.update(option_count=len(question['options']), probability_sum=sum(values),
                    minimum_probability=min(values), maximum_probability=max(values))
            else:
                cdf = candidate['continuous_cdf']
                validate_cdf(cdf, request, clipped=True)
                differences = [b - a for a, b in zip(cdf, cdf[1:])]
                row.update(cdf_length=len(cdf), minimum_increment=min(differences), maximum_increment=max(differences),
                    lower_cdf=cdf[0], upper_cdf=cdf[-1], lower_open=request['open_lower_bound'],
                    upper_open=request['open_upper_bound'])
            rows.append(row)
    report = {'schema': 'nonbinary-format-acceptance-v1', 'warning': WARNING,
        'metadata_sha256': digest(dataset), 'probability_policy': POLICY,
        'historical_cases': sum(r['fixture_kind'] == 'historical_metadata' for r in rows),
        'synthetic_controls': sum(r['fixture_kind'] == 'synthetic_control' for r in rows),
        'passed': len(rows), 'cases': rows, 'provider_http_requests': 0,
        'tavily_searches': 0, 'exa_searches': 0, 'forecasts_submitted': 0}
    save(output / 'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', type=Path, default=DEFAULT_METADATA)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.metadata, args.output), indent=2))

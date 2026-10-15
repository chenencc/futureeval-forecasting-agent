"""Versioned executable handoff contracts; legacy artifacts use explicit adapters."""
import hashlib
import math
from pathlib import Path

from .common import encoded, instant, read


class ContractError(ValueError):
    def __init__(self, code, field):
        self.code, self.field = code, field
        super().__init__(code + ': ' + field)


def validate(kind, value):
    from jsonschema import Draft202012Validator, FormatChecker
    schemas = read(Path(__file__).with_name('schemas.json'))
    schema = {'$ref': '#/$defs/' + kind, '$defs': schemas['$defs']}
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
    if errors:
        error = min(errors, key=lambda e: len(e.absolute_path))
        raise ContractError('schema_invalid', '.'.join(str(x) for x in error.absolute_path) or kind)
    try:
        encoded(value)
    except (ValueError, TypeError):
        raise ContractError('non_json_value', kind)
    if kind == 'packet':
        q = value['question']
        if not q['key'].startswith(q['platform'] + ':'):
            raise ContractError('question_identity_mismatch', 'question.key')
        if value['cutoff_policy'] == 'strict_as_of' and not value['as_of_utc']:
            raise ContractError('cutoff_missing', 'as_of_utc')
        if q['kind'] == 'multiple_choice' and len(q['options']) < 2:
            raise ContractError('options_missing', 'question.options')
        if q['kind'] in ('numeric', 'discrete', 'date') and not q['scale']:
            raise ContractError('scale_missing', 'question.scale')
        ids = set()
        for source in value['sources']:
            if source['id'] in ids:
                raise ContractError('duplicate_source_id', source['id'])
            ids.add(source['id'])
            if hashlib.sha256(source['text'].encode()).hexdigest() != source['body_sha256']:
                raise ContractError('body_hash_mismatch', source['id'])
            if not source['text'].strip():
                raise ContractError('empty_body', source['id'])
            if value['cutoff_policy'] == 'strict_as_of':
                if not source['captured_at'] or instant(source['captured_at']) > instant(value['as_of_utc']):
                    raise ContractError('source_after_cutoff_or_unknown', source['id'])
                if source['published_at'] and instant(source['published_at']) > instant(value['as_of_utc']):
                    raise ContractError('publication_after_cutoff', source['id'])
        if value['cutoff_policy'] == 'strict_as_of':
            if not value['as_of_utc'] or not q['observed_at'] or instant(q['observed_at']) > instant(value['as_of_utc']):
                raise ContractError('question_after_cutoff_or_unknown', 'question.observed_at')
        if value['cutoff_policy'] == 'capture_time_unbounded' and value['as_of_utc'] is not None:
            raise ContractError('ambiguous_cutoff', 'as_of_utc')
        if not value['sources'] and not any(g.get('code') == 'no_readable_originals' for g in value['gaps']):
            raise ContractError('empty_packet_without_gap', 'sources')
    if kind == 'forecast':
        if value['kind'] == 'binary':
            if type(value['value']) not in (float, int) or not 0 <= value['value'] <= 1:
                raise ContractError('invalid_probability', 'value')
        elif value['kind'] == 'multiple_choice':
            probs = value['value']
            if not isinstance(probs, dict) or len(probs) < 2 or any(type(x) not in (float, int) or not 0 <= x <= 1 for x in probs.values()) or abs(sum(probs.values()) - 1) > 1e-6:
                raise ContractError('invalid_categorical_distribution', 'value')
        else:
            knots = value['value']
            if not isinstance(knots, list) or len(knots) < 2:
                raise ContractError('invalid_cdf', 'value')
            previous = None
            for knot in knots:
                if not isinstance(knot, dict) or set(knot) != {'x', 'p'} or any(type(knot[k]) not in (int, float) or not math.isfinite(knot[k]) for k in ('x', 'p')):
                    raise ContractError('invalid_cdf', 'value')
                if not 0 <= knot['p'] <= 1 or previous and (knot['x'] <= previous['x'] or knot['p'] < previous['p']):
                    raise ContractError('invalid_cdf', 'value')
                previous = knot
    return value

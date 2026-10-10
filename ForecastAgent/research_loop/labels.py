"""Isolated evaluation contracts; source identity is distinct from outcome truth."""
import copy
import hashlib
import html
import math
from pathlib import Path

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.analysis.distributions import nominal, range_metadata

FIELDS = ('id', 'post_id', 'question_id', 'question', 'question_type',
          'resolution_criteria', 'fine_print', 'unit', 'options', 'scaling',
          'inbound_outcome_count', 'open_lower_bound', 'open_upper_bound')
SCHEMA = 'archived-label-binding-v1'


def contract(question):
    return {k: copy.deepcopy(question.get(k)) for k in FIELDS}


def text(value):
    return ' '.join(html.unescape(value or '').split())


def outcome_signature(label, question):
    kind = question['question_type']
    supplied = label.get('type', label.get('kind'))
    if supplied != kind:
        raise ValueError('Label question type does not match')
    if kind == 'binary':
        value = label.get('value')
        if isinstance(value, bool) or value not in (0, 1):
            raise ValueError('Binary outcome must be numeric zero or one')
        return {'kind': kind, 'value': float(value)}
    if kind == 'multiple_choice':
        value = label.get('resolution_display')
        options = question['options']
        if value not in options:
            raise ValueError('Outcome does not match an exact frozen option')
        if label.get('option_index_zero_based', options.index(value)) != options.index(value):
            raise ValueError('Option label and index conflict')
        return {'kind': kind, 'value': value}
    result_kind = label.get('result_kind')
    if result_kind in {'below_lower_bound', 'above_upper_bound'}:
        return {'kind': kind, 'interval': result_kind}
    if result_kind != ('date' if kind == 'date' else 'numeric'):
        raise ValueError('Outcome result kind does not match the question units')
    value = nominal(label['value'], range_metadata(question))
    if not math.isfinite(value):
        raise ValueError('Outcome must be finite')
    return {'kind': kind, 'value': value}


def bind(question, label, reference, provenance, conflicts=()):
    """Require a separately archived question/result record, not an ID-only label."""
    artifact = {'schema': SCHEMA, 'question_contract': contract(question),
        'question_sha256': digest(contract(question)), 'label': copy.deepcopy(label),
        'reference': copy.deepcopy(reference), 'reference_sha256': digest(reference),
        'provenance': copy.deepcopy(provenance), 'conflicts': copy.deepcopy(list(conflicts))}
    artifact['binding_sha256'] = digest(artifact)
    return artifact


def validate(question, artifact):
    errors = []
    if not isinstance(artifact, dict) or artifact.get('schema') != SCHEMA:
        return {'status': 'unbound_label', 'eligible': False, 'errors': ['ID-only labels cannot be scored']}
    try:
        if artifact['binding_sha256'] != digest({k: v for k, v in artifact.items() if k != 'binding_sha256'}):
            raise ValueError('Label binding checksum mismatch')
        if artifact['question_sha256'] != digest(contract(question)) or artifact['question_contract'] != contract(question):
            raise ValueError('Frozen question identity, rules, options, unit or scale changed')
        reference = artifact['reference']; saved = reference['question']; label = artifact['label']
        if artifact['reference_sha256'] != digest(reference):
            raise ValueError('Reference record checksum mismatch')
        if str(saved['id']) != str(question['id']) or saved['question_type'] != question['question_type']:
            raise ValueError('Archived result belongs to a different ID or type')
        if label.get('id') is not None and str(label['id']) != str(saved['id']):
            raise ValueError('Label ID mismatch')
        for k in ('post_id', 'question_id'):
            for supplied in (saved.get(k), label.get(k)):
                if question.get(k) is not None and supplied is not None and str(supplied) != str(question[k]):
                    raise ValueError('Platform '+k+' mismatch')
            if saved.get(k) is not None and label.get(k) is not None and str(saved[k]) != str(label[k]):
                raise ValueError('Source result '+k+' mismatch')
        for key in ('question', 'resolution_criteria', 'fine_print'):
            if text(saved.get(key)) != text(question.get(key)):
                raise ValueError('Archived '+key+' mismatch')
        if not text(saved.get('resolution_criteria')):
            raise ValueError('Archived resolution criteria missing')
        if question.get('unit') and saved.get('unit') != question['unit']:
            raise ValueError('Archived metric unit mismatch')
        if label.get('unit') and saved.get('unit') != label['unit']:
            raise ValueError('Label metric unit mismatch')
        if question['question_type'] == 'multiple_choice' and saved.get('options') != question['options']:
            raise ValueError('Archived options or ordering mismatch')
        if question['question_type'] in {'numeric', 'discrete', 'date'}:
            for key in ('scaling', 'inbound_outcome_count', 'open_lower_bound', 'open_upper_bound'):
                if saved.get(key) != question.get(key):
                    raise ValueError('Archived distribution metadata mismatch: '+key)
        if outcome_signature(label, question) != outcome_signature(reference['outcome'], question):
            raise ValueError('Label disagrees with independently archived result')
        provenance = artifact['provenance']
        if not provenance.get('source_url') or not provenance.get('observed_at') or not provenance.get('method'):
            raise ValueError('Label provenance incomplete')
        path = Path(provenance['source_path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != provenance['source_sha256']:
            raise ValueError('Archived label source checksum changed')
        for source in provenance.get('additional_sources', []):
            if hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() != source['sha256']:
                raise ValueError('Archived question source checksum changed')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        errors.append(str(exc))
    if artifact.get('conflicts'):
        errors.append('Unresolved metric, period or source-result conflict')
    return {'status': 'quarantined' if errors else 'archived_identity_bound',
        'eligible': not errors, 'errors': errors, 'truth_independently_verified': False,
        'limitation': 'Matches the archived result contract; does not certify source truth or prospective validity'}

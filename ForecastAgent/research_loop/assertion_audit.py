"""Opt-in typed audits of fallible assertions; never edit evidence or maps."""
import copy
import math
from ForecastAgent.analysis.pilot import digest

CHOICES = {
    'supported': 'Every material factual clause and scope qualifier is established by the assigned original excerpts.',
    'contradicted': 'An assigned original explicitly establishes an incompatible material fact.',
    'insufficient': 'At least one material clause is not established; missing information is not contradiction.'}
ASPECTS = {'fact', 'entity', 'time', 'metric', 'unit', 'stage', 'coverage', 'comparison'}

def prepare(question, as_of_utc, originals, assertions, *, mode='atomic'):
    """Caller-proposed qualifiers are hypotheses. Decomposition completeness is unproved."""
    if mode not in {'whole', 'atomic'}:
        raise ValueError('Unknown assertion audit mode')
    if not isinstance(originals, list) or not originals or not isinstance(assertions, list):
        raise ValueError('Originals and assertions must be explicit lists')
    sources = {}
    for source in originals:
        if not isinstance(source, dict):
            raise ValueError('Invalid original reference object')
        ident = source.get('evidence_id')
        if not isinstance(ident, str) or not ident or ident in sources:
            raise ValueError('Duplicate or invalid original reference')
        if not isinstance(source.get('text'), str) or not source['text'].strip():
            raise ValueError('Original reference has no readable text')
        sources[ident] = source
    hypotheses, questions, registry = {}, {}, []
    seen = set()
    for item in assertions:
        if not isinstance(item, dict):
            raise ValueError('Invalid assertion object')
        ident = item.get('id')
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValueError('Duplicate or invalid assertion ID')
        seen.add(ident)
        if not isinstance(item.get('text'), str) or not item['text'].strip():
            raise ValueError('Assertion requires nonempty hypothesis text')
        refs = item.get('reference_ids')
        if not isinstance(refs, list) or not refs or any(not isinstance(r,str) for r in refs) or len(set(refs)) != len(refs) or not set(refs) <= set(sources):
            raise ValueError('Assertion references must name distinct supplied originals')
        qualifiers = item.get('qualifiers')
        if not isinstance(qualifiers, list) or not qualifiers:
            raise ValueError('Assertion qualifiers are required even for the whole control')
        for qualifier in qualifiers:
            if not isinstance(qualifier,dict) or set(qualifier) != {'aspect','text'} or qualifier['aspect'] not in ASPECTS or not isinstance(qualifier['text'], str) or not qualifier['text'].strip():
                raise ValueError('Invalid atomic qualifier')
        parts = [{'aspect':'whole','text':item['text']}] if mode == 'whole' else qualifiers
        keys = []
        for index, part in enumerate(parts):
            key = 'H' + digest([ident, mode, index])[:20]
            if key in questions:
                raise ValueError('Decision head identity collision')
            hypotheses[key] = {'assertion_id':ident,'aspect':part['aspect'],
                'text':part['text'],'own_reference_ids':copy.deepcopy(refs)}
            questions[key] = {'type':'choice','criteria':copy.deepcopy(CHOICES),
                'instructions':'Audit ONLY hypothesis ' + key + ' against its own_reference_ids. '
                    'Hypotheses are not evidence. Respect date roles, metric definitions, '
                    'entity scope and stated conditions. Captions are not unseen pixels. '
                    'Compare delivered versions only, not the entire archive. '
                    'A page URL or publication date cannot supply an unstated event date. '
                    'Do not use other hypotheses as evidence or resolve a future forecast.'}
            keys.append(key)
        registry.append({'assertion_id':ident,'decision_keys':keys})
    if not registry:
        raise ValueError('No assertions to audit')
    state = {'question':copy.deepcopy(question),'as_of_utc':as_of_utc,
        'originals':copy.deepcopy(originals),'fallible_hypotheses':hypotheses,
        'scope':'Only delivered originals. No expected labels, outcomes, graph authority, or new retrieval.'}
    return {'state':state,'questions':questions,'registry':registry,'mode':mode,
        'decomposition_completeness_verified':False}

def summarize(prepared, response, *, threshold=0.8):
    """Conservative diagnostic conjunction; model agreement never certifies truth."""
    if isinstance(threshold, bool) or not isinstance(threshold, (int,float)) or not math.isfinite(threshold) or not 0.5 < threshold <= 1:
        raise ValueError('Invalid exploratory threshold')
    answers = response.get('answers', {}) if isinstance(response, dict) else {}
    if not isinstance(answers, dict):
        answers = {}
    rows = []
    for item in prepared['registry']:
        details = []
        for key in item['decision_keys']:
            answer = answers.get(key, {})
            if not isinstance(answer, dict):
                answer = {}
            probabilities = answer.get('probabilities', {})
            valid = (answer.get('type') == 'choice' and isinstance(probabilities, dict)
                and set(probabilities) == set(CHOICES) and all(
                    type(v) in (int,float) and math.isfinite(v) and 0 <= v <= 1 for v in probabilities.values())
                and abs(sum(probabilities.values())-1) <= 0.02)
            choice = answer.get('choice')
            coherent = valid and choice in CHOICES and probabilities[choice] >= max(probabilities.values()) - 1e-10
            judgment = choice if coherent and probabilities[choice] >= threshold else 'unreviewed'
            details.append({'key':key,'aspect':prepared['state']['fallible_hypotheses'][key]['aspect'],
                'judgment':judgment,'raw_choice':choice,'probabilities':copy.deepcopy(probabilities),
                'meaning_verified':False})
        states = {d['judgment'] for d in details}
        disposition = ('candidate_supported' if states == {'supported'} else
            'candidate_contradicted' if 'contradicted' in states else
            'insufficient' if 'insufficient' in states else 'unreviewed')
        rows.append({'assertion_id':item['assertion_id'],'disposition':disposition,
            'decisions':details,'meaning_verified':False})
    return {'mode':prepared['mode'],'rows':rows,'exploratory_threshold':threshold,
        'threshold_calibrated':False,'decomposition_completeness_verified':False,
        'material_receipts_granted':False,'graph_mutated':False,'forecasts_generated':False}

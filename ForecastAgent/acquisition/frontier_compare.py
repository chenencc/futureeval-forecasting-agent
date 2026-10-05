"""Bounded live-model comparison over immutable, already captured source material.

Only OpenRouter model requests are allowed. No search, source fetch, supplement,
analysis, forecast, or submission can be executed by this experimental harness.
"""
import argparse
import base64
import copy
import gzip
import hashlib
import json
import os
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.acquisition.pipeline import verify_baseline, prepare, resource_report
from ForecastAgent.readers.saved import select, version_digest
from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.supplement.stage import digest, save
from ForecastAgent.tavily_research import canonical_url

ROOT = Path(__file__).resolve().parents[2]
POOL = ROOT/'ForecastAgent/fixtures/intelligent_frontier_five.json.gz'
RUBRIC = ROOT/'ForecastAgent/experiments/intelligent_frontier_rubric.json'
V2_PROTOCOL = ROOT/'ForecastAgent/experiments/materials_v2_paired_protocol.json'
V3_PROTOCOL = ROOT/'ForecastAgent/experiments/materials_v3_paired_protocol.json'
MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free'
SUPER = 'nvidia/nemotron-3-super-120b-a12b:free'
LOCAL_TOOLS = {'plan_evidence', 'plan_channels', 'list_channels', 'list_sources',
    'list_documents', 'read_document', 'read_dataset_rows', 'search_saved_text',
    'find_passages', 'read_sources', 'record_quote', 'record_excerpt', 'record_excerpts',
    'review_passages', 'select_sources', 'collection_checkpoint', 'collection_acceptance',
    'record_channel_decision', 'set_acquisition_need_status', 'inspect_materials',
    'assess_materials', 'finish_collection', 'load_research_skill'}
POLICY = """
EXPERIMENT BOUNDARY: identical frozen leads and source bodies are supplied already.
No new discovery, fetch, Extract, archive, dataset collection, market query, or
refresh is possible. Use local read_sources/read_document and passage tools.
Do not plan another search or force network-channel obligations. Missing pages
outside the frozen body pool must be explicit unavailable material, not absence
of the event. Preserve exact dates, entity, units, metric, complete rows and
document identity in banked original excerpts. The experiment is retrospective
and unrestricted by publication date, not a cutoff-safe forecast. Do not infer
missing opening timestamps. Finish with gaps when useful permitted work ends.
Batch useful local actions within the existing twelve-decision dispatch limit.
"""


def inputs():
    verify_baseline()
    data = json.loads(gzip.decompress(POOL.read_bytes()))
    rubric = json.loads(RUBRIC.read_text(encoding='utf-8'))
    if hashlib.sha256(POOL.read_bytes()).hexdigest() != rubric['pool_sha256']:
        raise ValueError('Frozen material pool hash changed')
    if [c['id'] for c in data['cases']] != rubric['question_ids']:
        raise ValueError('Frozen case selection changed')
    for case in data['cases']:
        prepare(case['request'])
        for page in case['pages'].values():
            if hashlib.sha256(base64.b64decode(page['raw_response_base64'], validate=True)).hexdigest() != page['sha256']:
                raise ValueError('Original source response hash changed')
        for target in rubric['targets'][case['id']]:
            for alternative in target['alternatives']:
                body = case['pages'][alternative['url']]['content']
                if not all(anchor in body for anchor in alternative['anchors']):
                    raise ValueError('Rubric anchor not found in original body: '+target['id'])
    return data, rubric


def request_for(case, arm, *, repair_v2=False, repair_v3=False):
    if repair_v2 and repair_v3:
        raise ValueError('Choose one separately frozen candidate policy')
    request, _ = prepare(case['request'])
    request.update(mode='live', exa_search_policy='optional')
    if arm == 'baseline':
        if repair_v3:
            request['acquisition_strategy'] = 'intelligent_materials_v2'
        else:
            request.pop('acquisition_strategy')
    elif arm == 'candidate':
        # The preregistered experiment remains V1 despite the new pipeline default.
        request['acquisition_strategy'] = ('intelligent_materials_v3' if repair_v3 else
            'intelligent_materials_v2' if repair_v2 else 'intelligent_materials_v1')
    else:
        raise ValueError('Unknown comparison arm')
    return request


def snapshot_versions(bundle):
    return {u: {'raw_sha256':p.get('sha256'), 'parsed_sha256':version_digest(p)}
            for u,p in bundle['pages'].items()}


def seeded(case, arm, directory, *, repair_v2=False, repair_v3=False):
    request = request_for(case, arm, repair_v2=repair_v2, repair_v3=repair_v3)
    task = RetrievalTask(directory, request)
    if not task.path.exists():
        task.bundle['pages'] = copy.deepcopy(case['pages'])
        task.bundle['source_leads'].update(copy.deepcopy(case['source_leads']))
        task.bundle['acquisition_limits'].update(tavily_basic=0, exa_search=0)
        task.search_limit = 0
        task.exa_limit = 0
        task.bundle['frozen_frontier'] = {'pool_sha256':hashlib.sha256(POOL.read_bytes()).hexdigest(),
            'parent_bundle_sha256':case['parent_bundle_sha256'],
            'network_discovery_allowed':False, 'seeded_source_versions':snapshot_versions(task.bundle)}
        task.save()
    if snapshot_versions(task.bundle) != task.bundle['frozen_frontier']['seeded_source_versions']:
        raise ValueError('Saved source versions differ from frozen input')
    return task


def metrics(bundle, directory, case, targets):
    issues = []
    texts = {}
    for excerpt in bundle.get('excerpts', []):
        try:
            page, body, _ = select(bundle['pages'], excerpt['url'], excerpt.get('location',{}).get('document_index'))
            if body[excerpt['start_char']:excerpt['end_char']] != excerpt['text']:
                raise ValueError('Exact coordinates do not match')
            if page['sha256'] != excerpt['source_sha256'] or version_digest(page) != excerpt['source_parsed_sha256']:
                raise ValueError('Source version does not match')
            texts.setdefault(excerpt['url'], []).append(excerpt['text'])
        except (KeyError, ValueError, TypeError) as exc:
            issues.append({'excerpt_id':excerpt.get('id'), 'error':str(exc)})
    checks = []
    for target in targets:
        hits = []
        for alternative in target['alternatives']:
            text = '\n'.join(texts.get(alternative['url'], []))
            if all(anchor in text for anchor in alternative['anchors']):
                hits.append(alternative['url'])
        checks.append({'target_id':target['id'], 'description':target['description'],
            'selected':bool(hits), 'matching_sources':hits})
    if snapshot_versions(bundle) != {u:{'raw_sha256':p['sha256'], 'parsed_sha256':version_digest(p)} for u,p in case['pages'].items()}:
        issues.append({'error':'Frozen body inventory changed'})
    costs = resource_report(bundle, directory, directory/'no-supplement')
    network = sum(costs[k] for k in ('tavily_basic_attempts','exa_attempts','initial_source_http_attempts','extract_batches'))
    if network:
        issues.append({'error':'Network tool reservation occurred', 'attempts':network})
    return {'state':bundle.get('session_state'), 'termination_reason':(bundle.get('result') or {}).get('termination_reason'),
        'incomplete':bool((bundle.get('result') or {}).get('incomplete')),
        'last_error':bundle.get('last_error_detail'), 'resources':costs,
        'banked_excerpts':len(bundle.get('excerpts',[])), 'banked_sources':len(texts),
        'banked_chars':sum(len(e['text']) for e in bundle.get('excerpts',[])),
        'frozen_material_checks':checks, 'material_checks_selected':sum(c['selected'] for c in checks),
        'material_check_count':len(checks), 'issues':issues,
        'tool_errors':[{'tool':s.get('tool'), 'error':s['result']['error']} for s in bundle.get('transcript',[]) if s.get('result',{}).get('error')],
        'gaps':(bundle.get('result') or {}).get('gaps', []),
        'agent_material_report':(bundle.get('result') or {}).get('material_report'),
        'scope':'Exact frozen anchors in banked original text. A coverage proxy, not truth, event resolution, live recall, or forecast accuracy.'}


def run_arm(case, arm, directory, key, *, model=MODEL, http_cap=16, repair_v2=False, repair_v3=False):
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory/'comparison.json'
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding='utf-8'))
        raw = (directory/'bundle.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != report['bundle_sha256']:
            raise ValueError('Completed comparison bundle was changed')
        if json.loads(raw)['request'] != request_for(case,arm,repair_v2=repair_v2,repair_v3=repair_v3):
            raise ValueError('Completed comparison policy changed; no silent migration')
        return report
    task = seeded(case, arm, directory, repair_v2=repair_v2,repair_v3=repair_v3)
    # Interrupted physical reservations are not safe to silently repeat.
    if task.bundle.get('sessions'):
        raise ValueError('Interrupted comparison session requires review; refusing implicit redispatch')
    from ForecastAgent.runtime import tool_selection, guidance
    original_active = tool_selection.active_tools
    original_system = guidance.collection_system
    original_execute = RetrievalTask.execute

    def local_execute(self, name, args, token):
        if name == 'fetch_page' and canonical_url(args.get('url','')) in self.bundle['pages']:
            return self.page_view(self.bundle['pages'][canonical_url(args['url'])])
        if name not in LOCAL_TOOLS:
            self.bundle.setdefault('replay_blocked_actions', []).append({'tool':name, 'args':args})
            self.save()
            raise ValueError('Frozen replay forbids network acquisition and uncaptured bodies')
        return original_execute(self, name, args, token)

    def local_tools(task, tools, forced=None):
        return [t for t in original_active(task, tools, forced) if t['function']['name'] in LOCAL_TOOLS]

    started = time.monotonic()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {'FORECAST_MODEL':model,
            'FORECAST_MODEL_FALLBACK_SUPER':'0', 'EXA_API_KEY':'', 'TAVILY_API_KEY':''}))
        stack.enter_context(patch('ForecastAgent.runtime.retrieval.COLLECTION_HTTP_PER_DISPATCH',http_cap))
        stack.enter_context(patch.object(RetrievalTask,'execute',local_execute))
        stack.enter_context(patch.object(tool_selection,'active_tools',local_tools))
        stack.enter_context(patch.object(guidance,'collection_system',lambda task, skills:original_system(task,skills)+POLICY+
            '\nThis frozen replay further limits physical model HTTP attempts to '+str(http_cap)+'. No second dispatch.'))
        # Defense in depth: accidental physical source access is a hard failure.
        stack.enter_context(patch('ForecastAgent.providers.http.download', side_effect=RuntimeError('Source network disabled by frozen experiment')))
        bundle = run_retrieval(request_for(case,arm,repair_v2=repair_v2,repair_v3=repair_v3), directory, '', key)
    _, rubric = inputs()
    report = metrics(bundle, directory, case, rubric['targets'][case['id']])
    report.update(question_id=case['id'], arm=arm, elapsed_seconds=time.monotonic()-started,
        bundle_sha256=hashlib.sha256((directory/'bundle.json').read_bytes()).hexdigest())
    save(report_path, report)
    return report


def run_case(question_id, root, *, replicate_super=False, repair_v2=False, repair_v3=False):
    if repair_v2 and repair_v3:
        raise ValueError('Choose one separately frozen candidate policy')
    if (repair_v2 or repair_v3) and not replicate_super:
        raise ValueError('Repaired paired replication requires the fixed Super policy and eleven-request ceiling')
    data, rubric = inputs()
    case = next(c for c in data['cases'] if c['id']==question_id)
    model = SUPER if replicate_super else MODEL
    http_cap = 11 if replicate_super else 16
    identity = {'schema':'frozen-frontier-case-v3' if repair_v3 else 'frozen-frontier-case-v2' if repair_v2 else 'frozen-frontier-case-v1', 'pool_sha256':rubric['pool_sha256'],
        'rubric_sha256':hashlib.sha256(RUBRIC.read_bytes()).hexdigest(),
        'question_sha256':digest(case['request']), 'commit':os.environ.get('GITHUB_SHA'),
        'model':model, 'fallback':False, 'decisions_per_arm':12, 'http_ceiling_per_arm':http_cap,
        'replication_reason':'Ultra-only run 37287329839 failed all ten arms with upstream overload; separately freeze Super, same materials and lower equal ceilings.' if replicate_super else None,
        'search_calls':0,'source_fetch_calls':0,'analysis_calls':0,'submissions':0}
    if repair_v2:
        identity.update(candidate_strategy='intelligent_materials_v2', comparison_baseline_run=37288399817,
            preregistration_sha256=hashlib.sha256(V2_PROTOCOL.read_bytes()).hexdigest(),
            replication_reason='Fresh paired release-reading versus repaired V2 over the same five frozen bodies, model, arm order, rubric and common eleven-request ceiling. Never migrate prior tasks.')
    if repair_v3:
        from ForecastAgent.acquisition.pipeline import identity as pipeline_identity
        protocol = json.loads(V3_PROTOCOL.read_text(encoding='utf-8'))
        if (protocol['pool_sha256'] != rubric['pool_sha256'] or protocol['question_ids'] != rubric['question_ids']
                or protocol['primary_model'] != model or protocol['physical_http_ceiling_per_arm_per_question'] != http_cap
                or protocol['rubric_sha256'] != hashlib.sha256(RUBRIC.read_bytes()).hexdigest()):
            raise ValueError('V3 preregistration does not match the frozen experiment')
        identity.update(candidate_strategy='intelligent_materials_v3', baseline_strategy='intelligent_materials_v2',
            comparison_baseline_run=37297914625,
            preregistration_sha256=hashlib.sha256(V3_PROTOCOL.read_bytes()).hexdigest(),
            source_code_sha256=pipeline_identity(case['request'],False)['candidate_sha256'],
            replication_reason='Concurrent V2 versus V3 over identical preserved originals, Super, arm order and equal eleven-request ceilings; review control and temporal metadata changes only.')
    root = Path(root)
    root.mkdir(parents=True,exist_ok=True)
    with task_lock(root):
        path = root/'identity.json'
        if path.exists() and json.loads(path.read_text(encoding='utf-8')) != identity:
            raise ValueError('Frozen experiment identity changed')
        save(path,identity)
        key = os.environ.get('OPENROUTER_API_KEY','')
        if not key:
            raise ValueError('OPENROUTER_API_KEY required; no provider request attempted')
        order = rubric['arm_order'][question_id]
        result = {'question_id':question_id,'order':order,'arms':{}}
        for arm in order:
            result['arms'][arm] = run_arm(case, arm, root/arm, key,model=model,http_cap=http_cap,repair_v2=repair_v2,repair_v3=repair_v3)
            save(root/'paired.json',result)
        summary = {a:{k:r[k] for k in ('state','material_checks_selected','material_check_count','banked_excerpts','resources','issues')}
                   for a,r in result['arms'].items()}
        print(json.dumps({'question_id':question_id,'results':summary}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--question-id',choices=['36871','43494','43501','43991','44801'])
    parser.add_argument('--root',type=Path)
    parser.add_argument('--preflight',action='store_true')
    parser.add_argument('--replicate-super',action='store_true')
    parser.add_argument('--repair-v2',action='store_true')
    parser.add_argument('--repair-v3',action='store_true')
    args = parser.parse_args()
    data,rubric = inputs()
    if args.preflight:
        print(json.dumps({'cases':rubric['question_ids'],'sources':sum(len(c['pages']) for c in data['cases']),
            'material_checks':sum(len(t) for t in rubric['targets'].values()), 'pool_sha256':rubric['pool_sha256'],
            'new_model_calls':0,'new_network_acquisition_calls':0}))
    elif args.question_id and args.root:
        run_case(args.question_id,args.root,replicate_super=args.replicate_super,repair_v2=args.repair_v2,repair_v3=args.repair_v3)
    else:
        parser.error('Use --preflight or --question-id and --root')


if __name__=='__main__':
    main()

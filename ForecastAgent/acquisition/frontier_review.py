"""Audit saved paired artifacts without any model, search or source requests."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from ForecastAgent.acquisition.frontier_compare import inputs, metrics, request_for
from ForecastAgent.competition.mercury import packet_for
from ForecastAgent.analysis.mercury_evidence_chain import select
from ForecastAgent.local_sync import GitHub, import_zip
from ForecastAgent.supplement.stage import save

REPO = 'chenencc/futureeval-forecasting-agent'


def review(root, run_id):
    pool, rubric = inputs()
    rows = []
    for case in pool['cases']:
        directory = root/case['id']
        identity_path=directory/'identity.json'
        identity=json.loads(identity_path.read_text(encoding='utf-8')) if identity_path.exists() else {}
        row = {'question_id':case['id'], 'question':case['request']['question'], 'arms':{}}
        for arm in ('baseline','candidate'):
            bundle_path = directory/arm/'bundle.json'
            report_path = directory/arm/'comparison.json'
            if not bundle_path.exists() or not report_path.exists():
                row['arms'][arm] = {'state':'missing_or_interrupted'}
                continue
            bundle = json.loads(bundle_path.read_text(encoding='utf-8'))
            saved = json.loads(report_path.read_text(encoding='utf-8'))
            if saved['bundle_sha256'] != hashlib.sha256(bundle_path.read_bytes()).hexdigest():
                raise ValueError('Archived bundle checksum changed')
            record = metrics(bundle,directory/arm,case,rubric['targets'][case['id']])
            if any(record[k] != saved[k] for k in record):
                raise ValueError('Saved comparison counters differ from recomputed audit')
            observed_models = record['resources']['model_attempts_by_backend']
            if set(observed_models)-{identity.get('model',rubric['model'])}:
                record['issues'].append({'error':'Frozen model policy violated'})
            if record['resources']['model_http_attempts']>identity.get('http_ceiling_per_arm',16) or any(s.get('model_decisions',0)>12 for s in bundle.get('sessions',[])):
                record['issues'].append({'error':'Arm dispatch ceiling exceeded'})
            provider_errors=[]
            for attempt in bundle.get('model_attempts',[]):
                raw=json.loads((directory/arm/attempt['path']).read_text(encoding='utf-8'))
                if raw.get('model_routing',{}).get('fallback_active'):
                    record['issues'].append({'error':'Unexpected fallback'})
                payload=raw.get('response')
                if not isinstance(payload,dict):
                    try:
                        payload=json.loads(raw.get('response_body',''))
                    except (ValueError,TypeError):
                        payload={}
                error=payload.get('error') if isinstance(payload,dict) else None
                if raw['status']!='received':
                    provider_errors.append({'attempt_id':attempt['id'],'status':raw['status'],
                        'code':raw.get('http_status') or (error.get('code') if isinstance(error,dict) else None),
                        'message':error.get('message') if isinstance(error,dict) else raw.get('error')})
            packet=packet_for(bundle)
            projected,selection=select(packet)
            text={}
            for span in projected['evidence']:
                if bundle['pages'][span['url']]['content'][span['start']:span['end']]!=span['text']:
                    raise ValueError('Fixed analysis projection changed original coordinates')
                text.setdefault(span['url'],[]).append(span['text'])
            checks=[]
            for target in rubric['targets'][case['id']]:
                covered=any(all(anchor in '\n'.join(text.get(a['url'],[])) for anchor in a['anchors']) for a in target['alternatives'])
                checks.append({'target_id':target['id'],'first_analysis_visible':covered})
            record['fixed_analysis_projection']={'model_calls':0,'request_bytes':selection['request_bytes'],
                'visible_chars':sum(len(s['text']) for s in projected['evidence']),
                'context_omitted':projected['context_omitted'],'material_checks':checks,
                'selected_check_count':sum(c['first_analysis_visible'] for c in checks)}
            record['provider_statuses']={s:sum(a.get('status')==s for a in bundle.get('model_attempts',[]))
                for s in sorted({a.get('status','unknown') for a in bundle.get('model_attempts',[])})}
            record['provider_errors']=provider_errors
            record['elapsed_seconds']=saved['elapsed_seconds']
            record['frozen_case_identity']=identity
            record['selected_original_material']=[{'url':e['url'],'text':e['text'],
                'location':e.get('location'),'need_ids':e.get('need_ids')} for e in bundle.get('excerpts',[])]
            record['candidate_strategy']=bundle['request'].get('acquisition_strategy')
            names={s.get('tool') for s in bundle.get('transcript',[])}
            record['actual_tool_counts']={name:sum(s.get('tool')==name for s in bundle.get('transcript',[])) for name in sorted(names)}
            record['material_assessment_count']=len(bundle.get('material_assessments',{}))
            record['material_plan_failure_count']=len(bundle.get('control',{}).get('material_plan_failures',[]))
            record['pending_review_projection_count']=sum(bool(p.get('retained_pending_passage_ids')) for p in bundle.get('context_projections',[]))
            record['context_chars_max']=max((p.get('projected_chars',0) for p in bundle.get('context_projections',[])),default=0)
            if identity.get('candidate_strategy') == 'intelligent_materials_v3':
                if bundle['request'] != request_for(case,arm,repair_v3=True):
                    record['issues'].append({'error':'V3 arm request differs from its frozen policy'})
                record['execution_report']=(bundle.get('result') or {}).get('execution_report')
                record['material_adequacy_status']=(bundle.get('result') or {}).get('material_adequacy_status')
                record['review_batches_recorded']=len(bundle.get('material_review_batches',[]))
                record['deferred_passage_count']=((bundle.get('result') or {}).get('material_report') or {}).get('deferred_passage_count',0)
                record['rule_unknowns']=[{'need_id':n['id'],'fields':[r['field'] for r in n.get('rule_metadata_bindings',[]) if r['state']=='unknown']}
                    for n in bundle.get('plan') or [] if any(r['state']=='unknown' for r in n.get('rule_metadata_bindings',[]))]
            row['arms'][arm]=record
        rows.append(row)
    totals={}
    for arm in ('baseline','candidate'):
        available=[r['arms'][arm] for r in rows if 'resources' in r['arms'][arm]]
        totals[arm]={'reported_arms':len(available),
            'terminal_arms':sum(not r['incomplete'] for r in available),
            'incomplete_arms':sum(r['incomplete'] for r in available),
            'banked_material_checks':sum(r['material_checks_selected'] for r in available),
            'frozen_denominator':15,'model_http_attempts':sum(r['resources']['model_http_attempts'] for r in available),
            'known_reported_tokens':sum(r['resources']['known_reported_tokens'] for r in available),
            'unknown_usage_attempts':sum(r['resources']['unknown_usage_attempts'] for r in available),
            'fixed_first_analysis_visible_checks':sum(r['fixed_analysis_projection']['selected_check_count'] for r in available),
            'integrity_violations':sum(len(r['issues']) for r in available),
            'search_and_source_calls':sum(sum(r['resources'][k] for k in ('tavily_basic_attempts','exa_attempts','initial_source_http_attempts','extract_batches')) for r in available)}
    paired=[]
    for row in rows:
        arms=row['arms']
        eligible=all('resources' in arms[a] and not arms[a]['incomplete'] and not arms[a]['issues'] for a in ('baseline','candidate'))
        bounded=all('resources' in arms[a] and arms[a]['state'] in {'completed','completed_with_gaps','budget_exhausted'}
                    and not arms[a]['issues'] for a in ('baseline','candidate'))
        paired.append({'question_id':row['question_id'],'both_terminal_without_integrity_failure':eligible,
            'both_bounded_dispatches_auditable':bounded,
            'banked_material_check_delta':arms['candidate']['material_checks_selected']-arms['baseline']['material_checks_selected'] if bounded else None,
            'reason':'Banked material at the common ceiling is comparable; task completion and budget censoring are reported separately.' if bounded else 'Missing/interrupted/service-failed arms are not zero-quality observations.'})
    return {'schema':'frozen-frontier-paired-audit-v1','run_id':run_id,'pool_sha256':rubric['pool_sha256'],
        'results_complete':all('resources' in r['arms'][a] for r in rows for a in ('baseline','candidate')),
        'rows':rows,'totals':totals,'paired_comparisons':paired,
        'comparable_terminal_pairs':sum(p['both_terminal_without_integrity_failure'] for p in paired),
        'comparable_bounded_dispatch_pairs':sum(p['both_bounded_dispatches_auditable'] for p in paired),'promotion_allowed':False,
        'scope':'Development frozen-material navigation/selection replay. Original material inventory identical. Not fresh-search recall, strict blind review, event verification or forecast accuracy. Failed arms and all costs retained.'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--gh-path')
    parser.add_argument('--pull',action='store_true')
    parser.add_argument('--import-root',type=Path)
    args=parser.parse_args()
    args.root.mkdir(parents=True,exist_ok=True)
    if args.pull:
        gh=GitHub(args.gh_path)
        listing=gh.api(f'repos/{REPO}/actions/runs/{args.run_id}/artifacts?per_page=100')
        for artifact in listing['artifacts']:
            if not artifact['name'].startswith('intelligent-frontier-') or artifact['expired']:
                continue
            qid=artifact['name'].split('-')[-1]
            if qid not in {'36871','43494','43501','43991','44801'}:
                raise ValueError('Unexpected artifact identity')
            archive=args.root/(str(artifact['id'])+'.zip')
            if not archive.exists():
                gh.download(REPO,artifact['id'],archive)
            if args.import_root:
                import_zip(args.import_root,archive,repo=REPO,run_id=args.run_id,
                    artifact_id=artifact['id'],name=artifact['name'])
            with zipfile.ZipFile(archive) as z:
                for member in z.infolist():
                    target=(args.root/qid/member.filename).resolve()
                    if not target.is_relative_to((args.root/qid).resolve()):
                        raise ValueError('Artifact path escaped the case directory')
                z.extractall(args.root/qid)
    report=review(args.root,args.run_id)
    save(args.output,report)
    print(json.dumps({'results_complete':report['results_complete'],'totals':report['totals'],
        'rows':[{'question_id':r['question_id'],'arms':{a:{k:v[k] for k in ('state','material_checks_selected','material_check_count') if k in v} for a,v in r['arms'].items()}} for r in report['rows']]}))


if __name__=='__main__':
    main()

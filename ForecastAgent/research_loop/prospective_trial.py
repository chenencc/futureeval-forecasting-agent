"""One fresh acquisition, frozen originals, two matched Mercury forecasts.

This bounded local trial never submits. Official unresolved-status receipts stay
outside provider inputs. Finished collection and decision attempts are cached;
re-entry cannot renew provider allowances or silently retry failed scoring.
"""
import argparse
import copy
import hashlib
from pathlib import Path

from ForecastAgent.acquisition import pipeline
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.research_loop import decision, full_loop_trial, live_trial, map_paired_trial, state
from ForecastAgent.research_loop.forecast_brief import registry
from ForecastAgent.research_loop.target_pack import pack
from ForecastAgent.runtime.task_lock import task_lock

PROTOCOL = 'prospective-acquisition-map-common-originals-v1'
ARMS = ('original', 'mapped')


def prepared_pair(bundle, *, original_view=None):
    """A map may add interpretation, never original text absent from the control."""
    heads, spec = registry(bundle['request'])
    common, selection = pack(bundle if original_view is None else original_view, heads)
    pair = {arm: map_paired_trial.prepare(common, heads, spec, bundle if arm == 'mapped' else None)
            for arm in ARMS}
    mapped = pair['mapped']['state'].get('research_map')
    hidden = []
    if mapped:
        sources = {s['source_id']: s for s in common['sources']}
        for node in mapped['nodes']:
            # Decoded JSON quotes use view coordinates. They cannot be treated as
            # raw-body slices unless their complete literal quote is also visible.
            different_view = any(ref.get('view_sha256') and ref['view_sha256'] != ref['body_sha256']
                                 for ref in node.get('bindings', []))
            from ForecastAgent.research_loop import reference_map
            referenced = node.get('claim_origin') == reference_map.ORIGIN
            if different_view or (node['kind'] == 'observation' and not referenced and not any(
                    node['claim'] in e['text'] and any(
                        sources[e['source_id']]['url'] == ref['url'] and
                        sources[e['source_id']]['body_sha256'] == ref['body_sha256']
                        for ref in node.get('bindings', [])) for e in common['evidence'])):
                hidden.append(node['id'])
        mapped['nodes'] = [n for n in mapped['nodes'] if n['id'] not in hidden]
        kept = {n['id'] for n in mapped['nodes']}
        from ForecastAgent.research_loop import target_logic
        if target_logic.enabled(bundle):
            mapped['target_coverage'] = target_logic.audit(bundle, mapped['nodes'],
                [n for n in mapped['material_requests'] if set(n['node_ids']) <= kept])
        mapped['relations'] = [r for r in mapped['relations'] if {r['from_id'], r['to_id']} <= kept]
        mapped['material_requests'] = [r for r in mapped['material_requests'] if set(r['node_ids']) <= kept]
        mapped['hidden_node_ids'] = sorted(set(mapped.get('hidden_node_ids', [])) | set(hidden))
        mapped['factual_grounding_present'] = any(n['kind'] == 'observation' for n in mapped['nodes'])
        if hidden:
            mapped['supporting_path'] = 'Recheck delivered observations and original evidence; omitted claims are not evidence.'
            mapped['alternative_path'] = 'Recheck contrary interpretations from the same original evidence.'
        if not mapped['factual_grounding_present']:
            pair['mapped']['state'] = copy.deepcopy(common)
    for arm in ARMS:
        if any(pair[arm]['state'][k] != value for k, value in common.items()):
            raise ValueError('Scoring arm altered common original evidence')
        if pair[arm]['questions'] != heads:
            raise ValueError('Scoring registries differ')
    return common, pair, {'selection':selection, 'common_original_sha256':digest(common),
        'same_originals':True, 'same_scoring_heads':True,
        'literal_observations_omitted_from_scoring':hidden,
        'map_delivered':bool(pair['mapped']['state'].get('research_map'))}


def freeze(path, value):
    if path.exists() and load(path) != value:
        raise ValueError('Frozen scoring input changed: '+path.name)
    if not path.exists():
        save(path, value)


def run(parents, root, *, execute=False, limit_cases=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if not 1 <= len(parents) <= 5:
        raise ValueError('Use one to five frozen official inputs')
    identity = {'schema':PROTOCOL, 'parents':{str(Path(p).resolve()):live_trial.sha(p) for p in parents},
        'implementation':decision.implementation_hashes(), 'collection_budget':full_loop_trial.BUDGET,
        'scoring_http_per_arm':1, 'scoring_arms':list(ARMS),
        'submission_enabled':False, 'conditional_scoring_enabled':False,
        'outcomes_used':False, 'scoring_control':'Same frozen original text, target registry and Mercury; map is the only added field'}
    with task_lock(root):
        freeze(root/'identity.json', identity)
        save(root/'preregistration.json', {'primary_checks':['new captured bodies bound into later map revisions',
            'correct entity, metric, stage and target window', 'same delivered original evidence in both arms',
            'valid forecast payloads', 'actual physical requests, tokens, pending material and terminal gaps'],
            'future_evaluation':'Binary Brier and nonbinary distribution scores after official resolution; no accuracy claim before resolution',
            'common_collection_cost':'Shared by both arms; report separately from the scoring increment',
            'arm_order':'Alternates by case index', 'submitted':False})
        collection = full_loop_trial.run(parents, root/'collection', execute=execute,
            limit_cases=limit_cases, gap_feedback=True)
        rows, halt = [], collection.get('halt')
        for index, entry in enumerate(collection['rows']):
            ident = entry['id']; folder = root/'cases'/ident
            package = root/'collection/cases'/ident/'acquisition/package.json'
            raw = root/'collection/cases'/ident/'acquisition/collection/bundle.json'
            row = {'id':ident, 'question':entry.get('question'), 'collection_status':entry['status'],
                   'collection_summary':entry.get('summary'), 'arms':{}}
            if not execute or not package.exists():
                row['status'] = 'prepared' if not execute else 'preserved_incomplete_collection'
                rows.append(row)
                continue
            bundle = load(package)
            pipeline.reject_outcomes(bundle['request'])
            row['type'] = bundle['request']['question_type']
            row['package_sha256'] = live_trial.sha(package)
            try:
                common, pair, audit = prepared_pair(bundle)
                freeze(folder/'common-originals.json', common)
                freeze(folder/'pair-audit.json', audit)
                freeze(folder/'frozen-map.json', bundle.get('research_loop', {}).get('current'))
                freeze(folder/'source-inventory.json', {u:{'body_sha256':hashlib.sha256(p['content'].encode()).hexdigest(),
                    'characters':len(p['content']), 'capture_time':p.get('retrieved_at_utc')}
                    for u,p in bundle['pages'].items()})
                for arm in ARMS:
                    freeze(folder/arm/'prepared.json',pair[arm])
                row['pair_audit'] = audit
                row['map_audit'] = state.audit(copy.deepcopy(bundle))
                row['material_receipts'] = bundle.get('research_gap_feedback')
                order = list(ARMS) if index % 2 == 0 else list(reversed(ARMS))
                row['arm_order'] = order
                if halt:
                    row['status'] = 'held_for_provider_review'
                else:
                    for arm in order:
                        try:
                            result_path=folder/arm/'result.json'
                            result=load(result_path) if result_path.exists() else map_paired_trial.score(pair[arm],folder/arm)
                            row['arms'][arm]={'status':result['status'],'summary':result['summary'],
                                             'payload':result['payload']}
                        except Exception as exc:
                            failure={'status':'failed','error_type':type(exc).__name__,'message':str(exc)[:500]}
                            save(folder/arm/'failure.json',failure)
                            row['arms'][arm]=failure
                        attention=__import__('ForecastAgent.research_loop.fusion_trial',fromlist=['provider_attention']).provider_attention(folder/arm)
                        if attention:
                            halt={'reason':'provider_attention_required','records':attention}
                            break
                    row['status']='paired_scores_completed' if all(row['arms'].get(a,{}).get('status')=='completed' for a in ARMS) else 'scoring_incomplete'
                if live_trial.sha(package) != row['package_sha256']:
                    raise ValueError('Acquisition package mutated during scoring')
            except Exception as exc:
                row.update(status='preparation_failed',error_type=type(exc).__name__,message=str(exc)[:500])
            save(folder/'case-report.json',row)
            rows.append(row)
            save(root/'report.json', report(root, parents, rows, halt))
            print({'id':ident,'status':row['status'],'map_delivered':row.get('pair_audit',{}).get('map_delivered')},flush=True)
        result=report(root,parents,rows,halt)
        save(root/'report.json',result)
        return result


def report(root, parents, rows, halt):
    scoring_receipts=sorted((root/'cases').rglob('decision/http/*.json'))
    return {'schema':PROTOCOL,'requested':len(parents),'processed':len(rows),'rows':rows,'halt':halt,
        'collection_usage':live_trial.usage(sorted((root/'collection').rglob('model_calls/*.json'))),
        'scoring_usage':live_trial.usage(scoring_receipts),
        'paired_scores_completed':sum(r.get('status')=='paired_scores_completed' for r in rows),
        'forecast_quality_pending_resolution':True,'same_originals_required':True,'submitted':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parents',required=True,type=Path)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--limit-cases',type=int)
    args=parser.parse_args()
    result=run(load(args.parents),args.root,execute=args.execute,limit_cases=args.limit_cases)
    print({k:result[k] for k in ('requested','processed','paired_scores_completed','halt')},flush=True)


if __name__=='__main__':
    main()

"""Finite prospective three-arm trial, separate from production admission."""
import argparse
import copy
import os
from pathlib import Path

from . import SUPER
from .common import PublicHTTP, digest, instant, now, policy, read, seal, write
from .forecast import mean
from .official_map import VERSION, UPSTREAM, construct, scoring_state


def run(root, manifest):
    from .runtime import activate
    activate(manifest['engine_directory'])
    from ForecastAgent.research_loop import decision_http
    from ForecastAgent.analysis.mercury_evidence_chain import request_bytes
    from ForecastAgent.research_loop.decision import BYTE_CAP
    from .venues import refresh, book
    from .worker import provider_budget
    root = Path(root).resolve()
    if not 1 <= len(manifest['cases']) <= 3 or not 1 <= manifest['model_cap'] <= 15:
        raise ValueError('First official-map pilot is limited to three cases and fifteen model requests')
    if (root/'progress.json').exists() and read(root/'progress.json')['status'] == 'blocked':
        return read(root/'report.json')
    identity = {'schema': VERSION, 'manifest_sha256': digest(manifest)}
    if (root/'identity.json').exists() and read(root/'identity.json') != identity:
        raise ValueError('Frozen official-map trial identity changed')
    write(root/'identity.json', identity)
    ledger = read(root/'state.json') if (root/'state.json').exists() else copy.deepcopy(manifest['parent_worker_state'])
    old = manifest['parent_worker_state']
    n = len(old.get('provider_reservations', []))
    if ledger['tasks'] != old['tasks'] or ledger.get('provider_reservations', [])[:n] != old.get('provider_reservations', []):
        raise ValueError('Original tasks or provider reservations changed')
    os.environ['GITHUB_RUN_ID'] = root.name
    cfg = policy()
    cfg['maximum_worker_model_attempts_per_dispatch'] = manifest['model_cap']
    public = PublicHTTP(root/'public-http', cap=2*len(manifest['cases']))
    public.calls = len(list((root/'public-http').glob('*.json')))
    results = []
    stop_reason = None
    for case in manifest['cases']:
        folder = root/'cases'/digest(case['key'])
        result_path = folder/'result.json'
        if result_path.exists():
            results.append(read(result_path)); continue
        result = {'schema': VERSION, 'key':case['key'], 'family':case['market']['family'],
            'question':case['market']['question'], 'upstream_commit':UPSTREAM,
            'original_material_ref':case['material_ref'], 'arms':{},
            'new_admission':False, 'original_task_reopened':False, 'quota_reset':False}
        try:
            write(root/'progress.json', {'status':'running','key':case['key'],'finished':len(results),'at':now()})
            current_path, anchor_path = folder/'current-market.json', folder/'anchor.json'
            if anchor_path.exists():
                current, anchor = read(current_path), read(anchor_path)
            else:
                current, _ = refresh(public, case['market'])
                if current['rules_sha256'] != case['market']['rules_sha256'] or not current['tradable']:
                    raise ValueError('Frozen proposition changed or contract is not open')
                anchor = book(public,current,cfg)
                if anchor['status'] == 'shallow_book' and 0 < anchor.get('yes_bid',0) <= anchor.get('yes_ask',0) < 1:
                    anchor.update(status='usable', eligible_for_model_metrics=False, observational_context_only=True,
                        original_book_status='shallow_book', midpoint=(anchor['yes_bid']+anchor['yes_ask'])/2)
                write(current_path,current); write(anchor_path,anchor)
            if anchor['status'] != 'usable':
                raise ValueError('No usable matched price anchor')
            common = copy.deepcopy(case['original_state'])
            previous_map = copy.deepcopy(case['mapped_state'].get('research_map'))
            if any(v != case['mapped_state'].get(k) for k,v in common.items()):
                raise ValueError('Saved control arms do not share exactly the same originals')
            targets = case['price_schedule']['targets']
            heads = {k:v for k,v in case['questions'].items() if not k.startswith('price_') or
                instant(anchor['received_at']) < instant(targets[k.removeprefix('price_')])}
            common.update(anchor_at=anchor['received_at'],anchor_quote=anchor,
                price_schedule=case['price_schedule'])
            common['target_policy'] = dict(common.get('target_policy', {})) | {'anchor_quality_flags':anchor.get('quality_flags', [])}
            write(folder/'common-original-state.json',common); write(folder/'questions.json',heads)
            result.update(anchor=anchor, price_schedule=case['price_schedule'],
                common_original_sha256=digest(common), registry_sha256=digest(heads))
            with provider_budget(root,ledger,case['key'],cfg):
                graph = construct(common,folder/'official-map',model=manifest.get('map_model',SUPER))
                new_records = ledger.get('provider_reservations',[])[n:]
                if new_records and new_records[-1].get('http_status') in {401,402,403,429}:
                    raise RuntimeError('Account or rate/quota stop; no automatic model or key change')
                result['graph_status'] = graph['status']
                result['graph_audit'] = graph.get('audit')
                result['graph_sha256'] = graph['graph_sha256']
                states = {'original':copy.deepcopy(common)}
                if previous_map and previous_map.get('nodes'):
                    states['mapped'] = copy.deepcopy(common) | {'research_map':previous_map}
                if graph['status'] == 'accepted':
                    try:
                        states['official_map'] = scoring_state(common,graph)
                    except ValueError as exc:
                        result['graph_delivery_error'] = str(exc)
                for arm in ('original','mapped','official_map'):
                    if arm not in states:
                        result['arms'][arm] = {'status':'not_comparable','reason':'usable_bound_map_unavailable'}
                        continue
                    value = states[arm]
                    if any(value[k] != v for k,v in common.items()) or request_bytes(value,heads) > BYTE_CAP:
                        result['arms'][arm] = {'status':'not_comparable','reason':'matched_coverage_or_context_cap'}
                        continue
                    write(folder/arm/'state.json',value)
                    try:
                        response = decision_http.call(value,folder/arm/'decision',heads,required_questions=heads)
                        sealed = now()
                        result['arms'][arm] = {'status':'completed','sealed_at':sealed,
                            'price_means':{k.removeprefix('price_'):mean(response['answers'][k]) for k in heads if k.startswith('price_')},
                            'raw_eventual_probability_yes':response['answers']['event_yes']['noul'],
                            'eventual_probability_yes':min(.98,max(.02,response['answers']['event_yes']['noul'])),
                            'event_probability_is_not_future_price':True,'response_sha256':digest(response),
                            'state_sha256':digest(value),'map_delivered':arm != 'original',
                            'forecast_before_targets':{h:instant(sealed)<instant(target) for h,target in targets.items()},
                            'eligible_for_model_metrics':anchor.get('eligible_for_model_metrics',True) and
                                0 <= (instant(sealed)-instant(anchor['received_at'])).total_seconds()/60 <= cfg['maximum_seal_latency_minutes']}
                    except Exception as exc:
                        result['arms'][arm] = {'status':'failed','error_type':type(exc).__name__}
        except Exception as exc:
            result.update(status='failed',error_type=type(exc).__name__,error=str(exc)[:300])
        result.update(completed_at=now(),same_originals_and_heads=True,
            graph_accuracy_established=False,probability_coherence='not_requested_no_conditional_heads')
        if 'status' not in result:
            result['status'] = 'completed' if all(a.get('status')=='completed' for a in result['arms'].values()) and len(result['arms'])==3 else 'incomplete'
        write(result_path,result); write(root/'state.json',ledger)
        results.append(result)
        write(root/'results.json',{'schema':VERSION,'cases':results,'outcome_metrics_pending':True})
        current_records = ledger.get('provider_reservations',[])[n:]
        if current_records and current_records[-1].get('http_status') in {401,402,403,429}:
            stop_reason = 'account_or_rate_quota_stop'
            break
    added = ledger.get('provider_reservations',[])[n:]
    report = {'schema':VERSION,'cases':results,'new_model_http_attempts':sum(r['kind']=='model' for r in added),
        'model_http_cap':manifest['model_cap'],'public_http_attempts':public.calls,
        'original_task_states_unchanged':ledger['tasks']==old['tasks'],
        'original_provider_prefix_unchanged':ledger['provider_reservations'][:n]==old.get('provider_reservations',[]),
        'search_calls':0,'source_fetches':0,'trades':0,'submissions':0,'production_version_changed':False,
        'event_brier_and_price_errors_pending_labels':True,'stop_reason':stop_reason}
    write(root/'report.json',report); write(root/'progress.json',{'status':'blocked' if stop_reason else 'completed','finished':len(results),'at':now()}); seal(root)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    args = parser.parse_args()
    report = run(Path(args.root),read(Path(args.root)/'manifest.json'))
    print(__import__('json').dumps({'cases':len(report['cases']),'new_model_http_attempts':report['new_model_http_attempts'],
        'statuses':[c['status'] for c in report['cases']]},ensure_ascii=False))


if __name__ == '__main__':
    main()

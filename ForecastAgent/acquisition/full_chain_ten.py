"""Fresh acquisition, independent supplement and paired saved-evidence scoring.

This bounded experiment cannot submit. Labels are read only by offline review
after source identities and raw transport responses have been verified.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

from ForecastAgent.acquisition import handoff, handoff_scoring as scoring, pipeline
from ForecastAgent.acquisition.handoff_replay import audit_spans
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.pilot import digest, load, save, WARNING
from ForecastAgent.competition.mercury import packet_for
from ForecastAgent.providers import decisions
from ForecastAgent.runtime.task_lock import task_lock

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT/'ForecastAgent/experiments/full_chain_ten_inputs.json'
LABELS = ROOT/'ForecastAgent/experiments/full_chain_ten_labels.json'
PROTOCOL = ROOT/'ForecastAgent/experiments/full_chain_ten_protocol.json'
ARMS = ('old_packing', 'new_packing')


def protocol():
    pipeline.verify_baseline()
    p = load(PROTOCOL)
    for path, expected in p['files_sha256_lf'].items():
        if scoring.text_sha(ROOT/path) != expected:
            raise ValueError('Frozen ten-case dependency changed: '+path)
    if decisions.MODEL != p['decision_model'] or decisions.ENDPOINT != p['decision_endpoint']:
        raise ValueError('Frozen decision provider changed')
    cohort = load(INPUT)
    if cohort['question_ids'] != p['question_ids']:
        raise ValueError('Frozen cohort changed')
    for request in cohort['requests']:
        pipeline.prepare(request)
    return p, cohort


def first_state(bundle, arm):
    if arm == 'old_packing':
        return chain.select(packet_for(bundle))
    if arm == 'new_packing':
        return handoff.pack(bundle)
    raise ValueError('Unknown packing arm')


def reread_state(bundle, arm, first, reasons):
    # A controlled probe also tests cases the release would not reread. Its
    # score remains separate from the release's conditional production choice.
    focus = reasons or list(chain.CHECKS)
    packet = packet_for(bundle)
    if arm == 'new_packing':
        return scoring.extend(bundle, packet, first, focus)
    return chain.select(packet, first, focus, chain.SECOND_BYTES)


def score_route(bundle, folder, arm, p):
    folder = Path(folder)
    first, audit = first_state(bundle, arm)
    if audit_spans(bundle, first):
        raise ValueError('First input does not match original saved evidence')
    identity = {'schema':'full-chain-ten-score-route-v1', 'arm':arm,
                'bundle_sha256':digest(bundle), 'first_state_sha256':digest(first),
                'protocol_sha256_lf':scoring.text_sha(PROTOCOL),
                'registry_sha256':digest(chain.questions()), 'model':decisions.MODEL}
    if (folder/'identity.json').exists() and load(folder/'identity.json') != identity:
        raise ValueError('Scoring identity changed; do not restart the ledger')
    save(folder/'identity.json', identity)
    if (folder/'result.json').exists():
        return scoring.check_seal(folder)
    save(folder/'first-state.json', first)
    save(folder/'first-packing.json', audit)
    result = {'status':'failed', 'first_probability_yes':None,
              'reread_probability_yes':None, 'probability_yes':None,
              'production_probability_yes':None, 'submitted':False}
    try:
        first_response = chain.call(first, folder/'first')
        first_p = decisions.probability(first_response['answers']['event_yes']['noul'])
        result.update(status='completed', first_probability_yes=first_p,
                      probability_yes=first_p, production_probability_yes=first_p,
                      first_clipped_probability_yes=min(.98,max(.02,first_p)))
        reasons = chain.route(first_response)
        second, second_audit = reread_state(bundle, arm, first, reasons)
        new_chars = scoring.novel_chars(first, second)
        gate = {'release_reasons':reasons, 'new_original_chars':new_chars,
                'probe_eligible':new_chars >= p['minimum_new_chars'],
                'release_would_reread':bool(reasons) and new_chars >= p['minimum_new_chars']}
        save(folder/'routing.json', gate)
        result.update(status='completed', first_probability_yes=first_p,
                      probability_yes=first_p, production_probability_yes=first_p,
                      first_clipped_probability_yes=min(.98,max(.02,first_p)),
                      remaining_diagnostic_gaps=reasons, routing=gate,
                      reread_status='not_enough_new_original_text')
        if gate['probe_eligible']:
            if audit_spans(bundle, second):
                raise ValueError('Reread input does not match original saved evidence')
            save(folder/'second-state.json', second)
            save(folder/'second-packing.json', second_audit)
            try:
                second_response = chain.call(second, folder/'second')
                second_p = decisions.probability(second_response['answers']['event_yes']['noul'])
                result.update(reread_probability_yes=second_p, probability_yes=second_p,
                              reread_status='completed', remaining_diagnostic_gaps=chain.route(second_response))
                if gate['release_would_reread']:
                    result['production_probability_yes'] = second_p
            except Exception as exc:
                result.update(status='completed_with_reread_failure', reread_status='failed',
                              second_error=type(exc).__name__+': '+str(exc))
        result['clipped_probability_yes'] = min(.98,max(.02,result['probability_yes']))
    except Exception as exc:
        result['error'] = type(exc).__name__+': '+str(exc)
        if result['first_probability_yes'] is not None:
            result.update(status='completed_with_reread_failure', reread_status='failed',
                          clipped_probability_yes=min(.98,max(.02,result['probability_yes'])))
    return scoring.seal(folder, result)


def run_case(output, qid, *, dry_run=False):
    p, cohort = protocol()
    if qid not in p['question_ids']:
        raise ValueError('Question outside the frozen ten-case cohort')
    request = next(r for r in cohort['requests'] if r['id'] == qid)
    identity = {'schema':'full-chain-ten-case-v1','request_sha256':digest(request),
                'protocol_sha256_lf':scoring.text_sha(PROTOCOL),
                'model':p['collection_model'],'fallback':p['service_fallback'],
                'fresh_sources':True,'submitted':False}
    if dry_run:
        return {'question_id':qid,'status':'prepared','identity':identity,
                'acquisition':pipeline.identity(request,True),'provider_calls':0}
    if (os.environ.get('FORECAST_MODEL') != p['collection_model'] or
        os.environ.get('FORECAST_MODEL_FALLBACK_SUPER') != '1'):
        raise ValueError('Collection routing differs from the frozen policy')
    folder = Path(output)/qid
    folder.mkdir(parents=True,exist_ok=True)
    with task_lock(folder):
        if (folder/'identity.json').exists() and load(folder/'identity.json') != identity:
            raise ValueError('Case identity changed; do not reset state')
        save(folder/'identity.json',identity)
        case = {'question_id':qid,'stage':'acquisition','routes':{},'submitted':False}
        try:
            # One dispatch per invocation. Exact restoration may resume an
            # interrupted acquisition, without renewing physical reservations.
            executions = load(folder/'executions.json') if (folder/'executions.json').exists() else []
            stage = folder/'acquisition/state.json'
            complete = stage.exists() and load(stage).get('stage') == 'complete'
            if not complete:
                if len(executions) >= p['collection_max_executions']:
                    raise RuntimeError('Collection execution ceiling consumed; preserved state')
                executions.append({'status':'reserved'})
                save(folder/'executions.json',executions)
            try:
                acquisition = pipeline.run(request,folder/'acquisition',supplement_network=True)
            finally:
                if not complete:
                    executions[-1]['status'] = 'returned' if stage.exists() and load(stage).get('stage')=='complete' else 'preserved_incomplete'
                    save(folder/'executions.json',executions)
            save(folder/'acquisition-summary.json',acquisition)
            if acquisition.get('state') != 'complete':
                raise RuntimeError('Acquisition incomplete; preserve exact state for an authorized resume')
            package = folder/'acquisition/package.json'
            bundle = load(package)
            raw = package.read_bytes()
            case.update(stage='analysis', package_file_sha256=hashlib.sha256(raw).hexdigest())
            save(folder/'score-input-identity.json',{'package_file_sha256':case['package_file_sha256'],
                  'package_sha256':digest(bundle),'raw_collection_reused_between_arms':True})
            for arm in p['route_order'][qid]:
                case['routes'][arm] = score_route(bundle,folder/arm,arm,p)
            if package.read_bytes() != raw:
                raise ValueError('Analysis changed the source package')
            case['stage'] = 'complete'
        except Exception as exc:
            case['error'] = type(exc).__name__+': '+str(exc)
        save(folder/'case-result.json',case)
        return case


def review(output):
    p, cohort = protocol()
    root = Path(output)
    rows = []; attempts = []; errors = []; resources = []
    for request in cohort['requests']:
        qid = request['id']; folder = root/qid
        row = {'question_id':qid,'question':request['question'],'routes':{}}
        cp = folder/'case-result.json'
        row['case'] = load(cp) if cp.exists() else {'stage':'missing'}
        if (folder/'identity.json').exists():
            case_identity=load(folder/'identity.json')
            if case_identity['request_sha256']!=digest(request) or case_identity['protocol_sha256_lf']!=scoring.text_sha(PROTOCOL):
                raise ValueError('Case input or protocol changed')
        package = folder/'acquisition/package.json'
        if not package.exists():
            row['routes'] = {a:{'status':'missing'} for a in ARMS}
            rows.append(row)
            continue
        bundle = load(package)
        expected_request, _ = pipeline.prepare(request)
        if bundle['request'] != expected_request:
            raise ValueError('Acquisition input changed')
        state = load(folder/'acquisition/state.json')
        if hashlib.sha256(package.read_bytes()).hexdigest() != state['package_sha256']:
            raise ValueError('Completed package changed')
        if hashlib.sha256((folder/'acquisition/report.json').read_bytes()).hexdigest() != state['report_sha256']:
            raise ValueError('Acquisition report changed')
        collector = load(folder/'acquisition/collection/bundle.json')
        resources.append({'question_id':qid,'resources':pipeline.resource_report(collector,folder/'acquisition/collection',folder/'acquisition/supplement')})
        row['readable_captures'] = sum(bool(v.get('content')) for v in bundle.get('pages',{}).values())
        for arm in ARMS:
            directory = folder/arm
            if not (directory/'result.json').exists():
                row['routes'][arm] = {'status':'missing'}
                continue
            result = scoring.check_seal(directory)
            row['routes'][arm] = result
            identity = load(directory/'identity.json')
            first,_ = first_state(bundle,arm)
            if identity['bundle_sha256'] != digest(bundle) or identity['first_state_sha256'] != digest(first) or identity['protocol_sha256_lf'] != scoring.text_sha(PROTOCOL):
                raise ValueError('Source or first-state identity changed')
            for stage,cap in (('first',chain.FIRST_BYTES),('second',chain.SECOND_BYTES)):
                records = list((directory/stage/'http').glob('*.json'))
                if len(records)>1:errors.append({'question_id':qid,'arm':arm,'stage':stage,'error':'HTTP ceiling exceeded'})
                sp = directory/(stage+'-state.json')
                if sp.exists() and audit_spans(bundle,load(sp)):
                    raise ValueError('Observed analyst input fails source-coordinate integrity')
                for rp in records:
                    record = load(rp); req = record['request']
                    if req['model'] != decisions.MODEL or record['endpoint'] != decisions.ENDPOINT or digest(req['questions']) != digest(chain.questions()):
                        raise ValueError('Observed decision provider or registry changed')
                    if digest(req['state']) != digest(load(sp)) or len(json.dumps(req).encode()) > cap:
                        raise ValueError('Observed decision request changed or exceeds bound')
                    if stage=='first' and digest(req['state'])!=identity['first_state_sha256']:
                        raise ValueError('Actual first request differs from reproducible packing')
                    attempts.append({'question_id':qid,'arm':arm,'stage':stage,'status':record['status'],
                         'request_bytes':len(json.dumps(req).encode()),'usage':(record.get('response') or {}).get('usage'),
                         'record':str(rp.relative_to(root)),'sha256':hashlib.sha256(rp.read_bytes()).hexdigest()})
                response_path = directory/stage/'response.json'
                if response_path.exists():
                    response = decisions.validate(load(response_path),chain.questions())
                    received = [load(r) for r in records if load(r)['status']=='received']
                    if len(received)!=1 or digest(received[0]['response']) != digest(response):
                        raise ValueError('Response changed from actual provider journal')
                    key = 'first_probability_yes' if stage=='first' else 'reread_probability_yes'
                    if response['answers']['event_yes']['noul'] != result[key]:
                        raise ValueError('Prediction differs from raw provider response')
        rows.append(row)
    # No labels are opened while acquisition, scoring or the preceding audits run.
    if scoring.text_sha(LABELS)!=p['labels_sha256_lf']:
        raise ValueError('Frozen evaluation labels changed')
    labels = load(LABELS)['records']
    for row in rows:
        row['label_provenance'] = labels[row['question_id']]
        row['resolution'] = labels[row['question_id']]['resolution']
        for arm,result in row['routes'].items():
            for stage,key in (('first','first_probability_yes'),('reread','reread_probability_yes'),
                              ('final','probability_yes'),('production','production_probability_yes')):
                if result.get(key) is not None:
                    result[stage+'_scores'] = scoring.scores(result[key],row['resolution'])
    metrics = {}
    for stage in ('first','reread','final','production'):
        common = [r for r in rows if all(stage+'_scores' in r['routes'][a] for a in ARMS)]
        metrics[stage] = {'requested_pairs':len(rows),'scored_pairs':len(common), 'routes':{}}
        for arm in ARMS:
            values = [r['routes'][arm][stage+'_scores'] for r in common]
            metrics[stage]['routes'][arm] = {k:sum(v[k] for v in values)/len(values) if values else None
                for k in ('clipped_brier','clipped_log_loss','correct_at_half','raw_brier')}
    # First and second probabilities on the exact same eligible question set.
    paired_reread = {}
    for arm in ARMS:
        eligible = [r for r in rows if all(k+'_scores' in r['routes'][arm] for k in ('first','reread'))]
        paired_reread[arm] = {'n':len(eligible),'question_ids':[r['question_id'] for r in eligible],
            **{stage:sum(r['routes'][arm][stage+'_scores']['clipped_brier'] for r in eligible)/len(eligible) if eligible else None for stage in ('first','reread')}}
    for record in resources:
        r = record['resources']
        if r['tavily_basic_attempts']>3 or r['exa_attempts']>1 or r['initial_source_http_attempts']>8:
            errors.append({'question_id':record['question_id'],'error':'Source lifetime ceiling exceeded'})
    if len(attempts)>40:errors.append({'error':'Decision campaign ceiling exceeded'})
    known=0;unknown=0
    for a in attempts:
        u=a['usage'] or {}
        if type(u.get('input_tokens')) is int and type(u.get('output_tokens')) is int:
            known+=u['input_tokens']+u['output_tokens']
        elif type(u.get('total_tokens')) is int:known+=u['total_tokens']
        else:unknown+=1
    return {'schema':'full-chain-ten-review-v1','protocol':p,'rows':rows,'metrics':metrics,
            'same_cases_first_vs_reread':paired_reread,'attempts':attempts,'collection_resources':resources,
            'actual_mercury_http_attempts':len(attempts),'reported_mercury_tokens':known,'unknown_mercury_usage':unknown,
            'integrity_errors':errors,'complete':all(r['routes'].get(a,{}).get('probability_yes') is not None for r in rows for a in ARMS),
            'evaluation_warning':WARNING,'promotion_allowed':False,'submitted':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--question-id')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--review',type=Path)
    args=parser.parse_args()
    if args.review:
        result=review(args.output);save(args.review,result)
        print(json.dumps({k:result[k] for k in ('complete','metrics','same_cases_first_vs_reread','actual_mercury_http_attempts','reported_mercury_tokens','integrity_errors')}))
    else:
        result=run_case(args.output,args.question_id,dry_run=args.dry_run)
        print(json.dumps({k:result.get(k) for k in ('question_id','status','stage','error')},ensure_ascii=False))
        if not args.dry_run and (result.get('stage')!='complete' or any(r.get('probability_yes') is None for r in result['routes'].values())):
            raise RuntimeError('Incomplete case preserved; no implicit retry or budget reset')


if __name__=='__main__':main()

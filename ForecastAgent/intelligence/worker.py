"""Development shadow worker using frozen platform rules and the graph pipeline.

No submission interface is exposed. Restored tasks preserve source, implementation
and lifetime provider identities. Physical unknown attempts require review.
"""
import argparse
import copy
import hashlib
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.intelligence import pipeline
from ForecastAgent.intelligence.identity import code_identity
from ForecastAgent.releases import surfaces
from ForecastAgent.releases.v1_0_5 import validate_payload
from ForecastAgent.research_loop import dispatch, map_paired_trial, prospective_trial
from ForecastAgent.runtime.task_lock import task_lock

POLICY = {'schema':'graph-shadow-worker-v1', 'collection_model':
    'nvidia/nemotron-3-super-120b-a12b:free', 'decisions':12, 'model_http':16,
    'service_failures':4, 'collection_seconds':1500, 'process_seconds':2100,
    'map_limits':{'map_updates':6,'post_review_http':2,'post_review_revisions':2,'initial_http':8},
    'tavily_basic':3, 'exa':1, 'initial_fetch':8, 'extract_batches':1,
    'supplement_http':2, 'supplement_browser':2, 'scoring_http':1, 'reasoning_fallback_http':1,
    'submission_enabled':False, 'budget_reset':False}


def freeze(path, value):
    path=Path(path)
    if path.exists():
        if load(path)!=value:raise ValueError('Frozen shadow identity changed: '+path.name)
    else:save(path,value)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ReadOnlyClient:
    def __init__(self, base):self.base=base
    def account(self):return self.base.account()
    def post(self, ident):return surfaces.normalize(self.base.post(ident))
    def request(self,*args,**kwargs):raise ValueError('Shadow worker forbids platform writes')
    def comments(self,*args,**kwargs):raise ValueError('Shadow worker has no comment or delivery API')


def attempts(root):
    """Charge native and final review records once; verify native hash handles."""
    root=Path(root).resolve()
    files=set(root.glob('retrieval/**/model_calls/*.json')) | set(root.glob('final-map-review/model-http/*.json'))
    for path in root.glob('retrieval/**/collection/bundle.json'):
        bundle=load(path)
        for item in bundle.get('model_attempts',[]):
            if item.get('status')=='reserved':
                raise RuntimeError('Unknown model request reservation; provider review required')
            receipt=(path.parent/item['path']).resolve()
            if not receipt.is_relative_to(path.parent.resolve()) or not receipt.exists() or sha(receipt)!=item['sha256']:
                raise ValueError('Native model receipt identity changed')
            files.add(receipt)
    records=[load(p) for p in sorted(files)]
    if any(r.get('status') not in {'received','http_error','transport_error','missing_choices',
                                  'invalid_or_transport_error'} for r in records):
        raise RuntimeError('Unknown model request reservation; provider review required')
    return {'http':len(records),'decisions':sum(r.get('status')=='received' for r in records),
            'failures':sum(r.get('status')!='received' for r in records)}


@contextmanager
def remaining_budget(root):
    from ForecastAgent.runtime import retrieval
    used=attempts(root)
    names={'COLLECTION_MAX_TURNS':max(0,POLICY['decisions']-used['decisions']),
        'COLLECTION_HTTP_PER_DISPATCH':max(0,POLICY['model_http']-used['http']),
        'MODEL_FAILURES_PER_DISPATCH':max(0,POLICY['service_failures']-used['failures']),
        'MAX_RUN_SECONDS':POLICY['collection_seconds']}
    old={k:getattr(retrieval,k) for k in names}
    try:
        for key,value in names.items():setattr(retrieval,key,value)
        yield used
    finally:
        for key,value in old.items():setattr(retrieval,key,value)


def collect_child(request_path, directory, clock):
    directory=Path(directory)
    request=load(request_path)
    freeze(directory/'worker-policy.json',POLICY)
    with remaining_budget(directory):
        result=pipeline.collect(request,directory,clock_utc=clock,research_map=True,channel_tools=True)
    save(directory/'worker-collection-result.json',{'complete':bool((result.get('view') or {}).get('pages')),
        'raw_export_available':bool(result.get('view')),
        'status':result.get('status') or result.get('report',{}).get('status'),
        'collection_usage':attempts(directory), 'submitted':False})


def acquire(request_path, directory, clock):
    """The coordinator lock and native collector locks have distinct ownership."""
    from ForecastAgent.releases.guard import execute
    directory=Path(directory)
    number=len(list(directory.glob('worker-process-*.json')))+1
    report=execute([sys.executable,'-X','utf8','-m',__name__,'--collect-child',
        '--request',str(request_path),'--directory',str(directory),'--clock',clock],
        directory/f'worker-process-{number}.log',POLICY['process_seconds'],
        cwd=Path(__file__).resolve().parents[2])
    save(directory/f'worker-process-{number}.json',report)
    if report['status']!='completed':
        raise RuntimeError('Collection process '+report['status']+'; original reservations preserved')


class Adapter:
    def __init__(self, *, acquisition=None, score=None):
        self.acquisition=acquisition or acquire
        self.score=score or map_paired_trial.score

    def collect(self, request, retrieval):
        from ForecastAgent.intelligence.research_map import enable
        from ForecastAgent.channels.contracts import FIELD, POLICY as CHANNEL_POLICY
        retrieval=Path(retrieval);retrieval.mkdir(parents=True,exist_ok=True)
        job=retrieval/'graph-intelligence'
        # Re-enter with the original clock, not a new input or budget.
        clock_path=retrieval/'clock.json'
        if not clock_path.exists():save(clock_path,{'utc':datetime.now(timezone.utc).isoformat()})
        clock=load(clock_path)['utc']
        candidate=enable(copy.deepcopy(request),predictive_focus=True,scoring_delivery=True)
        candidate.update(as_of_utc=clock,collection_model=POLICY['collection_model'],
            acquisition_focus='raw_recall')
        candidate[FIELD]=CHANNEL_POLICY;candidate[dispatch.FIELD]=dispatch.POLICY
        candidate[dispatch.LIMIT_FIELD]=copy.deepcopy(POLICY['map_limits'])
        freeze(retrieval/'worker-identity.json',{'code':code_identity(),'policy':POLICY,'clock':clock,
            'request_sha256':digest(candidate),'submission_enabled':False})
        request_path=retrieval/'worker-request.json';freeze(request_path,candidate)
        marker=retrieval/'worker-bridge.json'
        if not marker.exists():
            self.acquisition(request_path,job,clock)
            result_path=job/'worker-collection-result.json'
            if result_path.exists() and load(result_path).get('raw_export_available') and not load(result_path)['complete']:
                raise RuntimeError('No readable material; raw export and original lifetime budgets preserved')
            if not result_path.exists() or not load(result_path)['complete']:
                raise RuntimeError('Collection interrupted; original lifetime budgets preserved')
            path=job/'final-map-package.json'
            if not path.exists():path=job/'retrieval/development-channels/package.json'
            package=load(path)
            if str(package['request']['id'])!=str(candidate['id']):raise ValueError('Package identity mismatch')
            bridge=copy.deepcopy(package)
            bridge['worker_handoff']={'source_path':str(path.relative_to(retrieval)), 'sha256':sha(path),
                'original_result':copy.deepcopy(package.get('result')), 'semantic_complete':False}
            # Only queue bookkeeping. The original result and complete package
            # remain unchanged and are used directly by inference.
            bridge['result']={'status':'collected','incomplete':False,'raw_export_only':True}
            save(retrieval/'bundle.json',bridge)
            save(marker,{'bridge_sha256':sha(retrieval/'bundle.json'),'package_sha256':sha(path),
                         'source_path':str(path.relative_to(retrieval))})
        return self.bridge(retrieval)

    @staticmethod
    def bridge(retrieval):
        retrieval=Path(retrieval);marker=load(retrieval/'worker-bridge.json')
        path=(retrieval/marker['source_path']).resolve()
        if not path.is_relative_to(retrieval.resolve()) or sha(path)!=marker['package_sha256'] or \
                sha(retrieval/'bundle.json')!=marker['bridge_sha256']:
            raise ValueError('Shadow handoff changed')
        return load(retrieval/'bundle.json')

    def supplement(self, adopted, folder, ident):
        # The integrated collector already supplemented and reviewed; do not
        # dispatch a second supplement or replace the frozen operating clock.
        bridge=self.bridge(Path(folder)/'retrieval')
        for field in ('question','resolution_criteria','fine_print'):
            if (adopted['request'].get(field) or '')!=(bridge['request'].get(field) or ''):
                raise ValueError('Current platform rules disagree with frozen handoff')
        return bridge

    def infer(self, input_path, folder, ident):
        from ForecastAgent.intelligence.admission import prepare
        folder=Path(folder);bridge=self.bridge(folder/'retrieval')
        source=(folder/'retrieval'/bridge['worker_handoff']['source_path']).resolve()
        package=load(source)
        if str(package['request']['id'])!=str(ident):raise ValueError('Scoring question identity mismatch')
        view,admission=prepare(package)
        if not view['pages']:raise RuntimeError('Material recovery cap exhausted without readable bodies')
        _,pair,audit=prospective_trial.prepared_pair(package,original_view=view)
        route='mapped' if audit['map_delivered'] else 'original'
        root=folder/'graph-score'
        freeze(root/'identity.json',{'source_sha256':sha(source),'prepared_sha256':digest(pair[route]),
            'route':route,'scoring_http_cap':POLICY['scoring_http'],
            'reasoning_fallback_http_cap':POLICY['reasoning_fallback_http'],'implementation':code_identity()})
        freeze(root/'pair-audit.json',audit);freeze(root/'admission.json',admission)
        result_path=root/'result.json'
        marker=root/'result-integrity.json'
        if result_path.exists() and marker.exists():
            if sha(result_path)!=load(marker)['sha256']:raise ValueError('Frozen scoring result changed')
        else:
            try:
                if (root/'failure.json').exists():
                    raise RuntimeError('Scoring lifetime cap exhausted; inspect preserved failure')
                receipts=list(root.glob('decision/http/*.json'))
                if receipts:
                    from ForecastAgent.providers.decisions import validate, MODEL, ENDPOINT
                    expected={'model':MODEL,'state':pair[route]['state'],'questions':pair[route]['questions']}
                    record=load(receipts[0])
                    if len(receipts)!=1 or record.get('endpoint')!=ENDPOINT or record.get('request')!=expected:
                        raise ValueError('Frozen scoring receipt changed')
                    if record.get('status')!='received':raise RuntimeError('Inspect completed scoring failure')
                    response=validate(record['response'],pair[route]['required_questions'])
                    # Recover the received/parsed cache window without a new HTTP.
                    freeze(root/'decision/response.json',response)
                elif result_path.exists():
                    raise ValueError('Scoring result exists without a physical receipt')
                result=self.score(pair[route],root)
            except Exception as exc:
                save(root/'failure.json',{'error':str(exc),'no_budget_reset':True,'submitted':False})
                if isinstance(exc,ValueError):raise
                from ForecastAgent.intelligence import scoring_recovery
                failure=scoring_recovery.completed_failure(pair[route],root)
                freeze(root/'fallback-authorization.json',failure)
                result=scoring_recovery.run(pair['original'],root/'reasoning-fallback')
                result['primary_failure']=failure
            validate_payload(package['request'],result['payload'])
            save(result_path,result);save(marker,{'sha256':sha(result_path)})
        result=load(result_path);validate_payload(package['request'],result['payload'])
        candidate={'payload':result['payload'],'selection':result.get('selection',route),'automatic':True,
            'prepared_route':route,
            'comment':'Development shadow forecast; no platform submission.',
            'source_sha256':sha(source),'result_sha256':digest(result),'pair_audit_sha256':digest(audit),
            'development_only':True,'submitted':False}
        save(folder/'candidate.json',candidate)
        save(folder/'candidate-integrity.json',{'sha256':sha(folder/'candidate.json')})
        return candidate

    def deliver(self, client, task, payload, comment, folder, *, enabled):
        folder=Path(folder)
        if sha(folder/'candidate.json')!=load(folder/'candidate-integrity.json')['sha256']:
            raise ValueError('Frozen candidate changed')
        candidate=load(folder/'candidate.json')
        if payload!=candidate['payload'] or comment!=candidate['comment']:
            raise ValueError('Shadow result identity changed')
        receipt={'status':'shadow_scored','payload_sha256':digest(payload),
                 'submitted':False,'development_only':True}
        freeze(folder/'shadow-result.json',receipt)
        return receipt


def run(root,snapshots,*,client=None,limit=5,adapter=None):
    if client is None:
        import os
        from ForecastAgent.competition.platform import Client
        client=Client(os.environ.get('METACULUS_TOKEN',''))
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    # A separate identity lock never overlaps the shadow queue's root lock.
    (root/'worker-identity-lock').mkdir(exist_ok=True)
    with task_lock(root/'worker-identity-lock'):
        freeze(root/'shadow-worker.json',{'policy':POLICY,'implementation':code_identity(),
            'platform_writes_disabled':True,'development_only':True})
    incoming=surfaces.snapshots(snapshots,root/'normalized-incoming')
    adapter=adapter or Adapter()
    from ForecastAgent.intelligence import shadow_queue
    report=shadow_queue.run(root,incoming,limit=limit,client=ReadOnlyClient(client),adapter=adapter)
    report.update(worker_policy=POLICY,development_only=True,submitted=False)
    save(root/'report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path);parser.add_argument('--snapshots',type=Path)
    parser.add_argument('--limit',type=int,default=5)
    parser.add_argument('--collect-child',action='store_true')
    parser.add_argument('--request',type=Path);parser.add_argument('--directory',type=Path)
    parser.add_argument('--clock')
    args=parser.parse_args()
    if args.collect_child:
        if not all((args.request,args.directory,args.clock)):parser.error('Child input required')
        collect_child(args.request,args.directory,args.clock)
    else:
        if not args.root or not args.snapshots:parser.error('Shadow state and official snapshots required')
        print(json.dumps(run(args.root,args.snapshots,limit=args.limit),indent=2))


if __name__=='__main__':main()

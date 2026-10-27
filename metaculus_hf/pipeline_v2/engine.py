"""Semantic operations shared by both platforms; no platform-specific pipeline forks."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import uuid
from .contracts import digest, now, snapshot_payload, PipelineError
from .repository import Repository, ensure
from .workflow import COLLECTION_ONLY, collection_only

ARMS = ("official_graph", "original")
ACTIVE = {"queued", "running", "retry_wait", "unknown"}
TERMINAL = {"succeeded", "failed", "skipped", "cancelled"}


def uid(): return uuid.uuid4().hex


def collection_profile(*, offline=False):
    """Same evidence acquisition, without any decision model or scoring arms."""
    result=profile(offline=offline)
    result['models'].pop('decider',None)
    result['methods']=dict(original='not-requested',official_graph='not-requested')
    result['parameters'].update(workflow_policy=COLLECTION_ONLY,analysis_arms=[],
        analysis_policy='not-requested',analysis_concurrency=0,
        intelligence_storage_layout='single-sealed-parquet-v1',seed_query_policy='independent-event-search-v2',
        retrieval_iteration_policy='read-candidates-before-repeat-search-v1',
        source_trace_policy='observed-primary-gap-search-v1',max_gap_searches=2)
    for key in ('allow_prior_only','decision_request_policy','numeric_postprocess',
                'mercury_input_budget_policy','official_graph_request_policy',
                'analysis_queue_policy','decision_packing_policy','original_input_policy'):
        result['parameters'].pop(key,None)
    return result


def find_run(record, run_id):
    matches = [run for run in record["runs"] if run["run_id"] == run_id]
    ensure(len(matches) == 1, "RUN_NOT_FOUND")
    return matches[0]


def profile(code_version=None, *, offline=False, analysis_arms=("original",)):
    ensure(tuple(analysis_arms) in (("original",), ARMS), "INVALID_ANALYSIS_ARMS")
    if code_version is None:
        root=__import__("pathlib").Path(__file__).resolve().parent
        files={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes().replace(b"\r\n",b"\n")).hexdigest() for p in root.rglob("*.py")}
        code_version="pipeline-v2-source-v2-sha256:"+digest(files)
    result=dict(code_version=code_version, acquisition_version="forecast-relevance-handoff-v7", models=dict(researcher="nvidia/nemotron-3.5-super-vl:free",decider="inception/mercury-decide:free"),
                methods=dict(official_graph="metaculus-c34f346-reference-bind-v2" if "official_graph" in analysis_arms else "disabled-original-only-v1", original="predictive-relevance-with-explicit-prior-v3"),
                budget_policy="persistent-reservations-shared-15rpm-v2", parameters=dict(lifecycle_policy="isolated-durable-task-recovery-v1",decision_request_policy="same-key-5xx-two-attempts-v1",allow_prior_only=not offline,offline=offline,analysis_arms=list(analysis_arms),analysis_policy="original-only-v1" if tuple(analysis_arms)==("original",) else "legacy-two-arm-v1",max_stage_attempts=3,capture_hard_timeout_seconds=600,capture_start_timeout_seconds=30,orderbook_download_policy="market-recorder-b6f3700-capability-bridge-v1",orderbook_storage_layout="checkpoint-delta-v2",orderbook_checkpoint_ticks=60,capture_dispatch_policy='prewarmed-one-shot-question-v1',analysis_queue_policy="snapshot-analysis-queue/1",resource_admission_policy="whole-question-resource-admission-v1",decision_packing_policy="arm-scoped-full-decision-envelope-v2",original_input_policy="forecast-relevant-independent-reading-v2",mercury_input_budget_policy="mercury-full-wire-byte-proxy-57344-v1",official_graph_request_policy="same-key-5xx-two-attempts-v1",body_review_request_policy="same-key-5xx-two-attempts-v1",initial_search_policy="exa+tavily-before-pages-v1",delivery_policy="arm-scoped-readable-blocks-54k-v4",evidence_admission_policy="evidence-admission-v2",numeric_postprocess="cdf-isotonic-v1"))
    result['parameters'].update(planner_request_policy='same-key-5xx-two-attempts-v1',planner_failure_handoff_policy='saved-candidates-two-pages-v1')
    if not offline:result['parameters']['post_capture_trade_history']='post-book-fixed-window-two-pass-v1'
    if not offline and __import__('os').environ.get('FORECAST_BROWSER_POOL'):
        result['parameters'].update(browser_policy='bounded-crawl4ai-pool-v1',browser_render_limit=2,
                                    browser_timeout_seconds=20,browser_request_limit=25)
    return result


class Engine:
    def __init__(self, repository): self.repo = repository

    def question(self, question_id, question_url, definition, *, observed_at=None, created_at=None, status="unknown", parent_event_id=None, post_id=None):
        observed = observed_at or now()
        definition = deepcopy(definition)
        for key, default in dict(options=[],scale={},condition=None,resolution_source_urls=[],instrument_mapping=None,close_at=None,expected_resolution_at=None).items():
            definition.setdefault(key,default)
        version = digest(dict(question_id=question_id, definition=definition))
        record = dict(schema_version="forecast-question-jsonl/1",revision=1,updated_at=observed,
            question=dict(id=question_id,url=question_url,parent_event_id=parent_event_id,post_id=post_id,created_at=created_at,
                first_observed_at=observed,last_observed_at=observed,first_open_observed_at=observed if status=="open" else None,
                observed_status=status,current_version_id=version,versions={version:dict(observed_at=observed,definition=definition)}),
            searches=[], evidence=dict(texts={},sources=[],snapshots=[]),runs=[],orderbook=None)
        try:
            return self.repo.create(record)
        except PipelineError as exc:
            if exc.code != "QUESTION_EXISTS": raise
        def update(old):
            q=old["question"]
            if version not in q["versions"]:q["versions"][version]=dict(observed_at=observed,definition=definition)
            if observed >= q["last_observed_at"]:
                q.update(url=question_url,current_version_id=version,last_observed_at=observed,observed_status=status)
                if status=="open" and q["first_open_observed_at"] is None:q["first_open_observed_at"]=observed
        return self.repo.commit_patch(question_id,None,update)

    def new_run(self, question_id, execution_profile, *, snapshot_id=None, based_on_run_id=None, mode="live", objective=None,
                opening_received_at=None, analysis_deadline_at=None, start_deadline_at=None):
        run_id = uid()
        def update(record):
            version=record["question"]["current_version_id"]
            if snapshot_id:
                snap=next((s for s in record["evidence"]["snapshots"] if s["snapshot_id"]==snapshot_id),None)
                ensure(snap is not None,"SNAPSHOT_NOT_FOUND");version=snap["question_version_id"]
            record["runs"].append(dict(run_id=run_id,question_version_id=version,snapshot_id=snapshot_id,based_on_run_id=based_on_run_id,
                mode=mode,objective=objective or {"kind":"event_outcome"},created_at=now(),opening_received_at=opening_received_at,
                start_deadline_at=start_deadline_at,analysis_deadline_at=analysis_deadline_at,execution_profile=execution_profile,stages={},
                results={arm:None for arm in ARMS},selection=None))
        self.repo.commit_patch(question_id,None,update)
        return run_id

    def disable_arm(self,qid,rid,arm):
        """One atomic terminal marker, no model, process or synthetic result."""
        ensure(arm in ARMS,'INVALID_ARM')
        def update(record):
            run=find_run(record,rid);enabled=run['execution_profile']['parameters'].get('analysis_arms',list(ARMS))
            ensure(arm not in enabled and run['selection'] is None and run['results'][arm] is None,'ARM_NOT_DISABLED')
            previous=run['stages'].get(arm,[])
            if previous:
                ensure(previous[-1]['status']=='skipped' and previous[-1]['error_code']=='ANALYSIS_ARM_DISABLED','DISABLED_ARM_ALREADY_STARTED');return
            at=now();run['stages'][arm]=[dict(attempt_id=uid(),status='skipped',queued_at=at,started_at=at,finished_at=at,first_external_request_at=None,owner_token=None,lease_until=None,error_code='ANALYSIS_ARM_DISABLED',error_summary=None,retryable=False,usage=None,receipt=None)]
        return self.repo.commit_patch(qid,None,update)

    def start(self, question_id, run_id, stage, *, lease_seconds=900):
        ensure(stage in {"admission","acquisition",*ARMS,"selection","submission"},"INVALID_STAGE")
        attempt_id,owner=uid(),uid()
        def update(record):
            run=find_run(record,run_id)
            if stage in {*ARMS,'selection','submission'}:
                ensure(not collection_only(run),'ANALYSIS_NOT_REQUESTED')
            ensure(run["selection"] is None or stage=="submission","RUN_FINALIZED")
            attempts=run["stages"].setdefault(stage,[])
            ensure(not any(a["status"] in ACTIVE for a in attempts),"STAGE_BUSY","Expired lease never proves old process is dead")
            ensure(not any(a["status"]=="succeeded" for a in attempts),"STAGE_ALREADY_SUCCEEDED")
            ensure(len(attempts)<run["execution_profile"]["parameters"].get("max_stage_attempts",3),"STAGE_ATTEMPT_LIMIT")
            if stage=="acquisition":ensure(run["snapshot_id"] is None,"CAPTURE_FINALIZED")
            if stage in ARMS:
                ensure(stage in run["execution_profile"]["parameters"].get("analysis_arms",ARMS),"ANALYSIS_ARM_DISABLED")
                ensure(run["snapshot_id"] is not None,"SNAPSHOT_REQUIRED")
                ensure(run["results"][stage] is None,"FINAL_RESULT_EXISTS")
            if stage=="submission":ensure(run["selection"] and run["selection"]["arm"] is not None,"NO_SUBMITTABLE_RESULT")
            at=now();lease=(datetime.fromisoformat(at.replace("Z","+00:00"))+timedelta(seconds=lease_seconds)).isoformat(timespec="microseconds").replace("+00:00","Z")
            attempts.append(dict(attempt_id=attempt_id,status="running",queued_at=at,started_at=at,finished_at=None,first_external_request_at=None,
                owner_token=owner,lease_until=lease,worker_ready_at=None,error_code=None,error_summary=None,retryable=False,usage=None,receipt=None))
        self.repo.commit_patch(question_id,None,update)
        return attempt_id,owner

    def owned(self, record, run_id, stage, attempt_id, owner):
        run=find_run(record,run_id)
        attempt=next((a for a in run["stages"].get(stage,[]) if a["attempt_id"]==attempt_id),None)
        ensure(attempt is not None and attempt["status"]=="running" and attempt["owner_token"]==owner,"STALE_OWNER")
        ensure(attempt["lease_until"]>now(),"LEASE_EXPIRED")
        return run,attempt

    def mark_http(self, question_id, run_id, stage, attempt_id, owner):
        at=now()
        def update(record):
            _,a=self.owned(record,run_id,stage,attempt_id,owner)
            if a["first_external_request_at"] is None:a["first_external_request_at"]=at
        self.repo.commit_patch(question_id,None,update)
        return at

    def mark_worker_ready(self,qid,rid,stage,aid,owner,ready_at):
        def update(record):
            _,attempt=self.owned(record,rid,stage,aid,owner)
            ensure(attempt.get("worker_ready_at") is None,"WORKER_READY_ALREADY_SET")
            attempt["worker_ready_at"]=ready_at
        return self.repo.commit_patch(qid,None,update)

    @staticmethod
    def terminal(attempt,status,code=None,summary=None,retryable=False,actual_usage=None,receipt=None):
        ensure(status in TERMINAL or status=="unknown","INVALID_TERMINAL_STATUS")
        attempt.update(status=status,finished_at=now(),owner_token=None,lease_until=None,error_code=code,
                       error_summary=summary[:500] if summary else None,retryable=retryable,receipt=receipt)
        if actual_usage is not None:attempt["usage"]=actual_usage

    def record_usage(self,qid,rid,stage,aid,owner,actual_usage):
        def update(record):
            _,a=self.owned(record,rid,stage,aid,owner);a["usage"]=actual_usage
        self.repo.commit_patch(qid,None,update)

    def finish(self,qid,rid,stage,aid,owner,status,**kwargs):
        ensure(status!="succeeded" or stage in {"admission","submission"},"ATOMIC_RESULT_COMMIT_REQUIRED")
        def update(record):
            _,a=self.owned(record,rid,stage,aid,owner);self.terminal(a,status,**kwargs)
        return self.repo.commit_patch(qid,None,update)

    def search(self,qid,rid,aid,owner,observation):
        ids=[]
        def update(record):
            self.owned(record,rid,"acquisition",aid,owner)
            search_id="q"+str(len(record["searches"])+1);ids.append(search_id)
            record["searches"].append(dict(observation,search_id=search_id,run_id=rid))
        self.repo.commit_patch(qid,None,update)
        return ids[0]

    def source(self,qid,rid,aid,owner,observation,text=None):
        ids=[]
        defaults=dict(final_url=None,title=None,publisher=None,search_id=None,search_result_rank=None,requested_at=None,captured_at=None,
                      extracted_at=None,published_at=None,locator=None,http_status=None,error_code=None)
        source={**defaults,**observation,"run_id":rid,"text_sha256":None}
        if text is not None:
            ensure(isinstance(text,str) and bool(text.strip()),"EMPTY_TEXT")
            source["text_sha256"]=hashlib.sha256(text.encode("utf-8")).hexdigest()
        if source["final_url"]==source["source_url"]:source["final_url"]=None
        def update(record):
            self.owned(record,rid,"acquisition",aid,owner)
            source["source_id"]="s"+str(len(record["evidence"]["sources"])+1);ids.append(source["source_id"])
            if text is not None:record["evidence"]["texts"].setdefault(source["text_sha256"],text)
            record["evidence"]["sources"].append(source)
        self.repo.commit_patch(qid,None,update)
        return ids[0]

    def seal(self,qid,rid,aid,owner,materials,*,gaps=None,context=None,temporal_policy="prospective",actual_usage=None,evidence_cutoff_at=None):
        snapshot_id=[]
        def update(record):
            run,a=self.owned(record,rid,"acquisition",aid,owner)
            ensure(run["snapshot_id"] is None,"CAPTURE_FINALIZED")
            at=now()
            snap=dict(snapshot_id="",question_version_id=run["question_version_id"],acquired_by_run_id=rid,sealed_at=at,evidence_cutoff_at=evidence_cutoff_at or at,
                temporal_policy=temporal_policy,materials=materials,context=context or {},gaps=gaps or [])
            snap["snapshot_id"]=digest(snapshot_payload(record,snap));snapshot_id.append(snap["snapshot_id"])
            record["evidence"]["snapshots"].append(snap);run["snapshot_id"]=snap["snapshot_id"]
            self.terminal(a,"succeeded",actual_usage=actual_usage)
            if context and 'final_review' in context:a['final_review']=deepcopy(context['final_review'])
        self.repo.commit_patch(qid,None,update)
        return snapshot_id[0]

    def commit_result(self,qid,rid,arm,aid,owner,prediction,actual_models,*,actual_usage=None,input_summary=None):
        ensure(arm in ARMS,"INVALID_ARM")
        def update(record):
            run,a=self.owned(record,rid,arm,aid,owner)
            ensure(run["snapshot_id"] is not None and run["selection"] is None and run["results"][arm] is None,"RUN_FINALIZED")
            run["results"][arm]=dict(prediction=prediction,actual_models=actual_models,produced_by_attempt_id=aid)
            if input_summary is not None:run['results'][arm]['input_summary']=deepcopy(input_summary)
            self.terminal(a,"succeeded",actual_usage=actual_usage)
        return self.repo.commit_patch(qid,None,update)

    def select(self,qid,rid):
        result=[]
        def update(record):
            run=find_run(record,rid)
            ensure(not collection_only(run),'ANALYSIS_NOT_REQUESTED')
            if run["selection"] is not None:result.append(run["selection"]);return
            for arm in ARMS:
                aa=run["stages"].get(arm,[])
                ensure(aa and aa[-1]["status"] in TERMINAL,"ANALYSIS_PENDING")
            arm="official_graph" if run["results"]["official_graph"] else "original" if run["results"]["original"] else None
            selection=dict(arm=arm,reason="OFFICIAL_PRIORITY" if arm=="official_graph" else ("ORIGINAL_ONLY" if run["execution_profile"]["parameters"].get("analysis_arms")==["original"] else "ORIGINAL_FALLBACK") if arm else "NO_VALID_FORECAST")
            run["selection"]=selection;result.append(selection)
            at=now()
            run["stages"]["selection"]=[dict(attempt_id=uid(),status="succeeded",queued_at=at,started_at=at,finished_at=at,first_external_request_at=None,
                owner_token=None,lease_until=None,error_code=None,error_summary=None,retryable=False,usage=None,receipt=None)]
        self.repo.commit_patch(qid,None,update)
        return result[0]

    def view(self,qid,rid=None,include_text=False):
        record=self.repo.get_question(qid)
        run=find_run(record,rid) if rid else record["runs"][-1] if record["runs"] else None
        snap=self.repo.read_snapshot(qid,run["snapshot_id"]) if run and run["snapshot_id"] else None
        if snap and not include_text:
            for material in snap["materials"]:material.pop("text")
        attempts=[a for aa in run["stages"].values() for a in aa] if run else []
        if not run:state="registered"
        elif run["selection"] is not None:state="scored" if run["selection"]["arm"] else "no_valid_forecast"
        elif any(a["status"] in ACTIVE for a in attempts):state="running"
        elif run["snapshot_id"]:state="analysis_pending"
        elif attempts and any(a["status"]=="failed" for a in run["stages"].get("acquisition",[])):state="acquisition_failed"
        else:state="pending"
        uses=[a["usage"] for a in attempts if a["usage"] is not None]
        cost=sum(u["cost_usd"] for u in uses) if uses and all(u.get("cost_usd") is not None for u in uses) else None
        derived=dict(state=state,readable_sources=sum(m["source"]["status"]=="extracted" for m in snap["materials"]) if snap else 0,
                     cost_usd=cost,record_bytes=self.repo.paths(qid)[0].stat().st_size)
        return dict(question=record["question"],revision=record["revision"],run=run,snapshot=snap,derived=derived,
                    searches=[s for s in record["searches"] if run and s["run_id"]==run["run_id"]])

"""Pure native-state import. Never starts jobs, resets budgets or reopens tasks."""
from copy import deepcopy
from datetime import datetime
from pipeline_v2.contracts import digest,now
from .runner import initial_state

NATIVE_TERMINAL={'accepted','already_forecasted','closed','deadline_missed','blocked_integrity','provider_blocked','platform_rejected'}
TIMERS={'forecastagent-official-monitor.timer','forecastagent-official-worker.timer'}
SERVICES={'forecastagent@official-monitor.service','forecastagent@official-worker.service'}

def convert(campaign,monitor,usage_days,drain_proof,*,archive_receipt,writer='metaculus-agent-actions-v1'):
    if campaign.get('schema')!='official-competition-v1' or not isinstance(campaign.get('tasks'),dict):raise ValueError('NATIVE_CAMPAIGN_SCHEMA')
    units=drain_proof.get('units',{})
    if not (TIMERS|SERVICES)<=set(units):raise ValueError('DRAIN_UNITS_REQUIRED')
    for name in TIMERS:
        if units[name].get('ActiveState')!='inactive' or units[name].get('UnitFileState')!='disabled':raise ValueError('NATIVE_TIMER_NOT_DISABLED')
    for name in SERVICES:
        if units[name].get('MainPID')!='0' or units[name].get('ActiveState') not in {'inactive','failed'}:raise ValueError('NATIVE_SERVICE_NOT_DRAINED')
    if not archive_receipt.get('verified') or not archive_receipt.get('revision') or not archive_receipt.get('manifest_sha256'):raise ValueError('VERIFIED_NATIVE_ARCHIVE_REQUIRED')
    state=initial_state(writer)
    for ident,t in campaign['tasks'].items():
        status=t.get('stage') or t.get('status')
        if not str(ident).isdecimal() or status not in NATIVE_TERMINAL:raise ValueError('NATIVE_NONTERMINAL_TASK')
        state['tasks'][str(ident)]=dict(id=str(ident),post_id=str(t.get('post_id','')),status='legacy_terminal',legacy_status=status,
            legacy_collection_executions=t.get('collection_executions',0),legacy_rule_identity=t.get('rule_identity'),legacy_task_hash=digest(t))
    days={}
    for day,counts in usage_days.items():
        datetime.strptime(day,'%Y-%m-%d');days[day]={}
        for kind,count in counts.items():
            if kind not in {'model','search','fetch'} or type(count)!=int or count<0:raise ValueError('NATIVE_QUOTA_COUNTER')
            days[day][kind]=count
    budget=dict(days=days,runs={},last_model_at=drain_proof.get('last_model_at',0),once={},key_usage={},
        migration=dict(policy='conservative-native-reservation-floor-v1',source_campaign_hash=digest(campaign),counter_sources=drain_proof.get('counter_sources',[]),
            historical_unknown_outcomes_consume_quota=True,budgets_reset=False))
    state.update(migration_complete=True,migration=dict(at=now(),source='amsterdam-native-official',campaign_payload_hash=digest(campaign),
        monitor_state_hash=digest(monitor),legacy_task_count=len(state['tasks']),drain_proof=deepcopy(drain_proof),archive=archive_receipt,
        budgets_reset=False,terminal_tasks_reopened=False))
    return state,budget

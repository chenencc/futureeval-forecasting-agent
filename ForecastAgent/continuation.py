"""Bounded continuation decisions for a frozen acquisition campaign."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
from ForecastAgent.collection_campaign import read, write, report
from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.runtime.limits import MODEL_HTTP_PER_DISPATCH


def time_value(value):
    if not value: return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None: raise ValueError('Continuation timestamps require a timezone')
    return parsed.astimezone(timezone.utc)


def next_batch(campaign, current=None):
    current = current or datetime.now(timezone.utc)
    result = {'schema':'campaign_continuation_v1', 'action':'stop', 'budget_reset':False,
              'forecast_submissions':False}
    if not campaign.get('continuation', {}).get('enabled'):
        return {**result, 'reason':'not_authorized'}
    if campaign.get('forecast_submissions') is not False:
        raise ValueError('Continuation is limited to acquisition-only campaigns')
    if campaign.get('requires_provider_review'):
        return {**result, 'reason':'provider_review_required'}
    if any(t['status']=='running' for t in campaign['tasks'].values()):
        return {**result, 'reason':'unfinished_owner_requires_reconciliation'}
    capacity = (campaign['limits']['model_http_campaign']-report(campaign)['resources']['model_http'])//MODEL_HTTP_PER_DISPATCH
    pending = [i for i,t in campaign['tasks'].items() if t['status']=='pending' and not t['attempts']]
    retry = [(i,t) for i,t in campaign['tasks'].items() if t['status']=='incomplete'
             and t.get('failure',{}).get('retryable') and len(t['attempts'])<campaign['limits']['executions_per_task']]
    if not pending and not retry:
        attention = [i for i,t in campaign['tasks'].items() if t['status'] not in {'acquired','closed_with_gaps'}]
        return {**result, 'reason':'requires_attention' if attention else 'queue_finished', 'attention_ids':attention}
    if capacity < 1:
        return {**result, 'reason':'campaign_model_budget_exhausted'}
    wait = time_value(campaign.get('pause_until_utc'))
    if pending:
        ids = pending[:min(5, capacity)]
        inputs = {'limit':str(len(ids)), 'questions':','.join(ids)}
    else:
        ident, entry = min(retry, key=lambda item: time_value(item[1].get('retry_after_utc')) or current)
        retry_at = time_value(entry.get('retry_after_utc'))
        if retry_at and (not wait or retry_at > wait): wait = retry_at
        inputs = {'limit':'1', 'question':ident,
                  'resume_reason':f'Authorized frozen campaign continuation after classified interruption; execution {len(entry["attempts"])+1}. Preserve all consumed budgets.'}
        ids = [ident]
    inputs['continue_remaining'] = 'true'
    if not inputs.get('resume_reason'):
        inputs['resume_reason'] = campaign['continuation']['authorization_reason']
    return {**result, 'action':'dispatch', 'question_ids':ids, 'inputs':inputs,
            'wait_until_utc':wait.isoformat() if wait and wait > current else None,
            'reason':'pending_batch' if pending else 'classified_retry'}


def plan(root, reason):
    if len(reason.strip())<20: raise ValueError('A substantive continuation authorization is required')
    root = Path(root)
    with task_lock(root):
        campaign = read(root/'campaign.json')
        chain = campaign.setdefault('continuation', {'enabled':True, 'batch_size':5,
            'authorization_reason':reason, 'authorized_at_utc':datetime.now(timezone.utc).isoformat(),
            'input_sha256':campaign['input_sha256'], 'budget_reset':False})
        if not chain['enabled'] or chain['input_sha256']!=campaign['input_sha256']:
            raise ValueError('Continuation authorization or frozen inputs changed')
        decision = next_batch(campaign)
        chain.setdefault('decisions', []).append(decision)
        write(root/'campaign.json', campaign)
        write(root/'continuation.json', decision)
        return decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--reason', required=True)
    args = parser.parse_args()
    import json
    print(json.dumps(plan(args.root,args.reason)))


if __name__ == '__main__': main()

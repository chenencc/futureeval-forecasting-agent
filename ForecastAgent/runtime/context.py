"""Deterministic model context projection; original conversation stays on disk."""
import json
import copy
from ForecastAgent.runtime.collection_v2 import eligible, model_view


def collection_context(task, recent_turns=3, max_recent_chars=32000):
    b = task.bundle
    messages = b['messages']
    starts = [i for i,m in enumerate(messages) if m.get('role') == 'assistant']
    start = starts[-recent_turns] if len(starts) >= recent_turns else starts[0] if starts else len(messages)
    # Move the boundary past whole turns only, preserving assistant/tool pairs.
    while len(json.dumps(messages[start:], ensure_ascii=False)) > max_recent_chars and start in starts and starts.index(start) < len(starts)-1:
        start = starts[starts.index(start)+1]
    blocked = {u for u,p in b['pages'].items() if task.verified_only and not eligible(p,task.cutoff)}
    state = {'schema':'collection_context_v1','request':b['request'],'plan':b['plan'],
             'entity_card':b.get('entity_card'),'channel_plan':b.get('channel_plan',[]),'budget':task.budget(),
             'sources':[{'url':u,'saved':u in b['pages'],'audit_only':u in blocked} for u in task.catalog()][:120],
             'documents':[{'url':u,'sha256':p.get('sha256'),'chars':len(p.get('content','')),
                           'diagnostics':p.get('body_diagnostics'),'unit':p.get('unit'),
                           'dataset':p.get('dataset'),'pagination':p.get('pagination')} for u,p in b['pages'].items()],
             'excerpts':[{'id':e['id'],'url':e['url'],'need_ids':e['need_ids'],'text':e['text'][:300]}
                         for e in b['excerpts'] if e['url'] not in blocked],
             'passage_ids':list(b.get('passages',{}))[-40:], 'channel_decisions':b.get('channel_decisions',{}),
             'market_snapshot_ids':list(b.get('market_snapshots',{})),
             'instruction':'Read saved documents/rows for full text. Older turns omitted from this model view remain in the durable transcript. Association does not prove correctness.'}
    projected = [messages[0], {'role':'user','content':json.dumps(model_view(state,blocked),ensure_ascii=False)}]
    recent=copy.deepcopy(messages[start:])
    def trim(value,limit):
        if isinstance(value,dict):
            result={k:trim(v,limit) for k,v in value.items()}
            if any(isinstance(v,str) and k in {'content','text','context','preview','snippet'} and len(v)>limit for k,v in value.items()):
                result['context_text_truncated']=True
            for k,v in value.items():
                if k in {'content','text','context','preview','snippet'} and isinstance(v,str): result[k]=v[:limit]
            return result
        if isinstance(value,list): return [trim(v,limit) for v in value]
        return value
    for limit in (1200,600,300):
        if len(json.dumps(recent,ensure_ascii=False))<=max_recent_chars: break
        for message in recent:
            if message.get('role')=='tool':
                try: message['content']=json.dumps(trim(json.loads(message['content']),limit),ensure_ascii=False)
                except (ValueError,TypeError): pass
    projected.extend(recent)
    # A checkpoint may have been appended after the most recent assistant turn.
    b.setdefault('context_projections',[]).append({'original_chars':len(json.dumps(messages,ensure_ascii=False)),
        'projected_chars':len(json.dumps(projected,ensure_ascii=False)), 'omitted_messages':start-1,
        'policy':'program_state_plus_complete_recent_turns_v1'})
    return projected

"""Bound candidate expansion without dropping any delivered source unit."""
import copy
from ForecastAgent.supplement import acquisition_contract as base


def prepare(prepared, *, group_chars=20000, trigger=12):
    original=[r for r in prepared['candidates'].values() if r['kind']=='source']
    if len(original)<=trigger:return prepared
    out=copy.deepcopy(prepared);groups=[]
    for row in original:
        if (groups and groups[-1]['url']==row['url'] and groups[-1]['end']==row['start']
                and row['end']-groups[-1]['start']<=group_chars):
            g=groups[-1];g['end']=row['end'];g['text']+=row['text']
            g['member_passage_ids'].append(row['passage_id'])
            for context in row.get('context_spans',[]):
                if context not in g.setdefault('context_spans',[]):g['context_spans'].append(copy.deepcopy(context))
        else:
            groups.append({**copy.deepcopy(row),'member_passage_ids':[row['passage_id']]})
    mapping={}
    for g in groups:
        if len(g['member_passage_ids'])>1:
            g['passage_id']='G-'+base.sha(str(g['member_passage_ids']))[:20]
        for ref in g['member_passage_ids']:mapping[ref]=g['passage_id']
    out['candidates']={r['passage_id']:r for r in groups}
    out['candidates'].update({k:v for k,v in prepared['candidates'].items() if v['kind']=='rule'})
    out['state']['candidate_groups']=[{k:g[k] for k in ('passage_id','member_passage_ids','url','start','end')} for g in groups]
    out['state']['candidate_group_policy']='Groups contain only contiguous original delivered units from one source; all text stays in reading.passages. Read every member listed in candidate_groups. Grouping establishes no semantic entailment.'
    for registry in out['registry']:
        entries=registry.get('entries') or [registry]
        for entry in entries:
            key=entry.get('key') or registry['key']
            question=out['questions'][key];options={};choices={}
            for choice,definition in entry['choices'].items():
                oldref=definition['reference_id'];newref=mapping.get(oldref,oldref)
                newchoice=choice.replace(oldref,newref) if oldref else choice
                choices[newchoice]={**definition,'reference_id':newref}
                text=question['criteria'][choice]
                options[newchoice]=(text.replace(oldref,newref)+' Read its members in state.candidate_groups.'
                                    if oldref in mapping else text)
            question['criteria']=options;entry['choices']=choices
    out['candidate_group_audit']={'original_source_units':len(original),'grouped_candidates':len(groups),
        'dropped_units':[],'reading_unchanged':out['state']['reading']==prepared['state']['reading']}
    return out

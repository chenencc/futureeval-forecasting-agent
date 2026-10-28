"""Deterministic reading aids over delivered literal records, not event facts."""
import re

def selected_winner_labels(packet,state):
    library={e['evidence_id']:e for e in packet['evidence']};groups={}
    for ident,meta in packet['evidence_packing']['candidates'].items():
        if meta.get('coverage_group')!='winner_records' or meta.get('kind')!='record_fact':continue
        e=library[ident]
        if not any(x['source_id']==e['source_id'] and x['start']<=e['start'] and x['end']>=e['end'] for x in state['evidence']):continue
        # Count exact saved labels; never normalize names, infer missing rows,
        # reconcile sources, or claim this is the final season's winner count.
        match=re.search(r'(?m)^\s*Winner:\s*\n([^\n]+)',e['text'])
        if not match:continue
        groups.setdefault(e['source_id'],[]).append((ident,match[1].strip()))
    result=[]
    for source,rows in sorted(groups.items()):
        available=sum(m.get('coverage_group')=='winner_records' and m.get('kind')=='record_fact' and m['source_id']==source for m in packet['evidence_packing']['candidates'].values())
        labels=sorted({label for _,label in rows})
        result.append(dict(source_id=source,operation='distinct_exact_Winner_labels_in_delivered_records',
            record_count=len(rows),distinct_label_count=len(labels),exact_labels=labels,
            complete_saved_record_selection=len(rows)==available,available_saved_records=available,
            scope='Selected saved records only; no entity resolution, source truth, full-season coverage or forecast implied.',
            evidence_ids=[ident for ident,_ in rows]))
    return result

def attach(packet,state):
    result=selected_winner_labels(packet,state)
    if result:state['literal_calculations']=result
    else:state.pop('literal_calculations',None)

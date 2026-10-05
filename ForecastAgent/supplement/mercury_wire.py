"""Keep semantic reading, move large audit-only ledgers out of model context."""
import copy


def compact_rule_links(value):
    if isinstance(value,list):return [compact_rule_links(v) for v in value]
    if not isinstance(value,dict):return value
    return {k:([{'rule_id':r['rule_id']} for r in v] if k in ('rule_bindings','origins')
               and isinstance(v,list) and all(isinstance(r,dict) and 'rule_id' in r for r in v)
               else compact_rule_links(v)) for k,v in value.items()}


def prepare(prepared):
    out=copy.deepcopy(prepared)
    state=out['state']
    for key in ('needs','observation_contracts','candidate_claims'):
        if key in state:state[key]=compact_rule_links(state[key])
    reading=state.get('reading')
    if reading:
        metadata={k:reading.pop(k,[]) for k in ('inventory','omitted')}
        # Hashes/table offsets certify local identity; the model needs the
        # exact source text, URL, coordinates and any separately saved header.
        reading['passages']=[{k:p[k] for k in ('passage_id','url','start','end','text','context_spans') if k in p}
                             for p in reading['passages']]
        state['reading_coverage_notice']={
            'saved_sources':len(metadata['inventory']),
            'omitted_saved_units':len(metadata['omitted']),
            'omission_does_not_prove_absence':True,
            'full_inventory_and_omission_ledger_retained_outside_model_context':True}
    for row in state.get('candidate_claims',[]):
        for claim in [row['root']]+row['qualifiers']:
            binding=claim.get('binding')
            if binding:
                binding.pop('text',None)
                for span in binding.get('context_spans',[]):span.pop('text',None)
            if claim.get('decision'):
                claim['decision']={k:v for k,v in claim['decision'].items() if k!='probabilities'}
    return out

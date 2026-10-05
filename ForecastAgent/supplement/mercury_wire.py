"""Keep semantic reading, move large audit-only ledgers out of model context."""
import copy


def prepare(prepared):
    out=copy.deepcopy(prepared)
    state=out['state']
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

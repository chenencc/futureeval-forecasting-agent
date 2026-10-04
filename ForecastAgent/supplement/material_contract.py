"""Experimental outcome-neutral material contracts; production gates are unchanged."""
from urllib.parse import urlsplit
from ForecastAgent.supplement.quote_alignment import bind as bind_quote

ROLES=('status_record','forecast_reporting','court_opinion','docket_record',
       'benchmark_method','benchmark_rows','historical_snapshot','access_document',
       'squad_announcement','unknown')
RELATIONS=('supports_event','counterevidence','neutral','unknown')


def evaluate(contract,source,observation):
    """Material fit consumes explicit observations, never event polarity.

    Semantic fields remain reviewer claims. Exact quotation and source-domain
    gates are deterministic; they do not prove the semantic claims are correct.
    """
    result={'status':'unreviewed','issues':[],'evidence_relation':'unknown','truth_verified':False,'closure_scope':contract.get('closure_scope','target_material'),
            'eligible_for_need_closure':False}
    if observation is None:return result
    if observation.get('reading_failed') is True:
        return {**result,'status':'reading_failed','issues':['saved_body_read_failed']}
    if observation.get('evidence_relation') not in RELATIONS:
        return {**result,'status':'uncertain','issues':['invalid_evidence_relation']}
    result['evidence_relation']=observation['evidence_relation']
    quote=observation.get('quote')
    aligned=bind_quote(source,quote)
    result['quote_binding']=aligned
    if not aligned['bound']:
        return {**result,'status':'uncertain','issues':['unbound_quote']}
    if contract.get('publisher_required'):
        domains=contract.get('publisher_domains',[])
        if not domains:
            return {**result,'status':'source_unresolved','issues':['publisher_contract_unresolved']}
        host=(urlsplit(source.get('url','')).hostname or '').lower()
        if not any(host==d or host.endswith('.'+d) for d in domains):
            result['issues'].append('publisher_origin_mismatch')
    role=observation.get('document_role')
    if role not in ROLES or role=='unknown':
        return {**result,'status':'uncertain','issues':result['issues']+['document_role_unresolved']}
    if role not in contract['allowed_document_roles']:result['issues'].append('document_role_mismatch')
    axes=observation.get('fit_axes',{})
    for axis in contract['required_axes']:
        if type(axes.get(axis)) is not bool:
            return {**result,'status':'uncertain','issues':result['issues']+['missing_fit_axis:'+axis]}
        if axes[axis] is False:result['issues'].append('fit_axis_mismatch:'+axis)
    return {**result,'status':'mismatched' if result['issues'] else 'matched',
            'eligible_for_need_closure':not result['issues'] and result['closure_scope']=='target_material'}

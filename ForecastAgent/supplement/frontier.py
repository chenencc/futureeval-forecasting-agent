"""Bounded evidence-oriented frontier; every deferral remains inspectable."""
import re
from collections import Counter
from urllib.parse import unquote, urlsplit
from ForecastAgent.evidence.source_identity import alias_family
from ForecastAgent.evidence.source_coverage import calendar_dates
from ForecastAgent.evidence.source_checks import inspect_body

POLICY = {'linked_total':16, 'linked_per_parent':4, 'linked_per_host':8}
MATERIAL_POLICY = {'linked_total':32, 'linked_per_parent':8, 'linked_per_host':16}


def material_policy(question):
    return MATERIAL_POLICY if (question.get('budget_profile')=='solid_v2' and
        question.get('experiment_id') and question.get('pipeline')=='collection') else POLICY
NAV = r'/(?:faqs?|privacy|terms|contact|careers|login|signin|feeds)(?:/|\.|$)'


def subject(question):
    # Routing anchor only; no claim that every capitalized phrase is an entity.
    phrases = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4}', question.get('question',''))
    for phrase in phrases:
        phrase = re.sub(r'^(?:Will|When|What|How|The)\s+', '', phrase)
        if len(phrase.split()) >= 2:
            return phrase.lower()
    return None


def admit(sources, question, retained=(), pages=None):
    pages = pages or {}
    linked = [s for s in retained if s.get('origin') == 'saved_link']
    parents = Counter(s.get('parent_url') for s in linked)
    hosts = Counter(urlsplit(s['url']).hostname for s in linked)
    families = {alias_family(s['url']) for s in retained}
    captured = {alias_family(u) for u,p in pages.items()
                if inspect_body(question,u,p.get('content',''))['eligible_for_evidence']}
    anchor = subject(question)
    expected_days = calendar_dates(question.get('question',''))
    accepted = []; deferred = []
    for source in sorted(sources, key=lambda s:(not s['rule_primary'], -s['score'], s['url'])):
        url = source['url']; family = alias_family(url)
        path = urlsplit(url).path; host = urlsplit(url).hostname
        text = unquote(url+' '+source.get('label','')).lower()
        linked_source = source.get('origin') == 'saved_link'
        dependency = source.get('material_dependency', {})
        material_role = dependency.get('role','ordinary_detail')
        dependency_route = material_role != 'ordinary_detail'
        selected_policy = material_policy(question) if dependency_route else POLICY
        reason = None
        if not source['rule_primary'] and re.search(NAV,path,re.I):
            reason = 'navigation_route'
        elif (question.get('collection_workflow') == 'material-gap-v1' and not source['rule_primary'] and
              re.search(r'/(?:about|team|careers|subscribe|newsletter|cookie|legal)(?:/|\.|$)', path, re.I) and
              material_role == 'ordinary_detail'):
            reason = 'background_navigation_route'
        elif not source['rule_primary'] and family in families:
            reason = 'possible_url_alias_or_already_queued'
        elif not source['rule_primary'] and family in captured:
            reason = 'saved_body_available_no_repeat'
        elif linked_source and not source['rule_primary']:
            detail_file = bool(re.search(r'\.(?:pdf|csv|xlsx?|txt|json|xml)$',path,re.I))
            dated_file = detail_file and bool(set(expected_days)&set(calendar_dates(text)))
            literal_anchor = anchor and anchor in re.sub(r'[^a-z0-9]+',' ',text)
            topic_match = len(source['source_contract']['topic_matches']) >= 2
            if dependency.get('different_explicit_day') and dependency.get('parent_collection_state')=='directory_only':
                reason = 'different_target_day_file'
            elif not literal_anchor and not dated_file and not topic_match and not dependency_route:
                reason = 'weak_detail_anchor'
            elif anchor and not literal_anchor and not detail_file and not dependency_route:
                reason = 'different_named_subject_or_unobserved_subject'
            elif len(linked) >= selected_policy['linked_total']:
                reason = 'linked_total_ceiling'
            elif parents[source.get('parent_url')] >= selected_policy['linked_per_parent']:
                reason = 'parent_branch_ceiling'
            elif hosts[host] >= selected_policy['linked_per_host']:
                reason = 'host_branch_ceiling'
        if reason:
            deferred.append({**source,'deferred_reason':reason,'equivalence_verified':False})
            continue
        accepted.append(source); families.add(family)
        if linked_source:
            linked.append(source); parents[source.get('parent_url')] += 1; hosts[host] += 1
    return {'accepted':accepted, 'deferred':deferred, 'policy':POLICY,'material_policy':material_policy(question),
            'subject_routing_anchor':anchor,'full_recall_verified':False}

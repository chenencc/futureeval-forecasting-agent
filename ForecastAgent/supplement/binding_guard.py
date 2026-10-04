"""Deterministic material closure gates, independent of the reviewing model."""
import re
from urllib.parse import urlsplit
from ForecastAgent.tools.channels import PUBLISHERS
from ForecastAgent.supplement import requirement_contract
from ForecastAgent.supplement import witness_contract


def source_requirement(need):
    """Named publisher documents require publisher origin, not copied attribution.

    Explicit per-need domains support organizations outside the channel registry.
    Unknown official publishers remain unresolved; no domain is guessed by an LLM.
    Secondary-source needs intentionally retain their own acquisition role.
    """
    text = need.get('condition', '')
    explicit = need.get('required_source_domains', [])
    if explicit:
        return {'required':True, 'domains':list(explicit), 'origin':'explicit_need_contract', 'role':'issuer_original'}
    # A disjunction of news and official announcements permits either origin.
    # Explicit domain contracts above still take precedence.
    if re.search(r'\b(?:news|media|press|reporting)\b[^.;]{0,40}\bor\b[^.;]{0,30}\bofficial\b|\bofficial\b[^.;]{0,40}\bor\b[^.;]{0,30}\b(?:news|media|press|reporting)\b',text,re.I):
        return {'required':False, 'domains':[], 'origin':'alternative_source_roles', 'role':'primary_or_secondary'}
    if re.search(r'\b(?:news|media)\s+(?:outlets|sources|reports|articles)\b',text,re.I) and not re.search(r'\bofficial\s+(?:news|media)\s+(?:outlets|sources|reports|articles)\b',text,re.I):
        return {'required':False, 'domains':[], 'origin':'secondary_source_need', 'role':'secondary_reporting'}
    if re.search(r'\bsecondary\b|\battribut(?:ing|ion)\b|\bthird.party\b|\b(?:media|news|press)\b.*\b(?:report|coverage)', text, re.I):
        return {'required':False, 'domains':[], 'origin':'secondary_source_need', 'role':'secondary_reporting'}
    publishers = [p for p in PUBLISHERS if any(re.search(r'\b'+re.escape(a)+r'\b', text,
        0 if a.isupper() and len(a)<=5 else re.I) for a in p['aliases'])]
    publishers = [p for p in publishers if p.get('origin_policy')!='explicit_official_only' or
                  re.search(r'\bofficial\b|\bissuer\b|\bprimary source\b',text,re.I)]
    return {'required':bool(publishers) or bool(re.search(r'\bofficial\b|\bprimary source\b',text,re.I)),
            'domains':sorted({d for p in publishers for d in p['domains']}),
            'origin':'publisher_registry' if publishers else 'unmapped_official_requirement',
            'role':'issuer_original' if publishers or re.search(r'\bofficial\b|\bprimary source\b',text,re.I) else 'unspecified'}


def required_axes(need):
    """Program-owned applicability; unknown needs keep all four requirements.

    Access/method documentation need not contain an observation's numeric value
    or date. Explicit dated method versions retain their period requirement.
    Event documents still require the event period, but no numeric metric.
    """
    condition = need.get('condition', '')
    if re.search(r'\b(?:access method|data access|subscription|methodology|metadata|access instructions|how to access)\b', condition, re.I):
        axes = ['entity','material_type']
        if re.search(r'\b(?:version|effective|as of|dated)\b',condition,re.I):
            axes.append('period')
        return axes
    if re.search(r'\b(?:starting lineup|line.up|participation|election date|announcement date)\b',condition,re.I):
        return ['entity','material_type','period']
    event=re.search(r'\b(?:held|commenced|begun|published|released|announced|appointed|assumed office)\b',condition,re.I)
    numeric=re.search(r'\b(?:number of|count|price|prices|capitalization|index|percentage|votes|score|scores|seats|market cap|measurement|amount|value)\b',condition,re.I)
    if event and not numeric:
        return ['entity','material_type','period']
    return ['entity','material_type','metric','period']


def assess(need, binding, deferred_urls=()):
    requirement = source_requirement(need)
    hostname = (urlsplit(binding.get('url','')).hostname or '').lower()
    origin_matches = any(hostname == d or hostname.endswith('.'+d) for d in requirement['domains'])
    issues = []
    if binding.get('url') in deferred_urls:
        issues.append('binding_conflicts_with_source_deferral')
    if requirement['required'] and not origin_matches:
        issues.append('required_publisher_origin_unverified')
    issues.extend(binding.get('reading_issues', []))
    issues.extend(requirement_contract.issues(need,binding))
    issues.extend(witness_contract.issues(need,binding))
    return {'eligible_for_material_closure':not issues, 'issues':issues,
            'source_requirement':requirement, 'publisher_origin_observed':origin_matches,
            'truth_verified':False}

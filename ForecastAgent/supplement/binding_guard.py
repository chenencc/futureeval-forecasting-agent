"""Deterministic material closure gates, independent of the reviewing model."""
import re
from urllib.parse import urlsplit
from ForecastAgent.tools.channels import PUBLISHERS


def source_requirement(need):
    """Named publisher documents require publisher origin, not copied attribution.

    Explicit per-need domains support organizations outside the channel registry.
    Unknown official publishers remain unresolved; no domain is guessed by an LLM.
    Secondary-source needs intentionally retain their own acquisition role.
    """
    text = need.get('condition', '')
    explicit = need.get('required_source_domains', [])
    if explicit:
        return {'required':True, 'domains':list(explicit), 'origin':'explicit_need_contract'}
    if re.search(r'\bsecondary\b|\battribut(?:ing|ion)\b|\bthird.party\b', text, re.I):
        return {'required':False, 'domains':[], 'origin':'secondary_source_need'}
    publishers = [p for p in PUBLISHERS if any(re.search(r'\b'+re.escape(a)+r'\b', text,
        0 if a.isupper() and len(a)<=5 else re.I) for a in p['aliases'])]
    return {'required':bool(publishers) or bool(re.search(r'\bofficial\b|\bprimary source\b',text,re.I)),
            'domains':sorted({d for p in publishers for d in p['domains']}),
            'origin':'publisher_registry' if publishers else 'unmapped_official_requirement'}


def assess(need, binding, deferred_urls=()):
    requirement = source_requirement(need)
    hostname = (urlsplit(binding.get('url','')).hostname or '').lower()
    origin_matches = any(hostname == d or hostname.endswith('.'+d) for d in requirement['domains'])
    issues = []
    if binding.get('url') in deferred_urls:
        issues.append('binding_conflicts_with_source_deferral')
    if requirement['required'] and not origin_matches:
        issues.append('required_publisher_origin_unverified')
    return {'eligible_for_material_closure':not issues, 'issues':issues,
            'source_requirement':requirement, 'publisher_origin_observed':origin_matches,
            'truth_verified':False}

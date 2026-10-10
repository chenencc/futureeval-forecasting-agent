"""Opt-in native acquisition maps and matched, original-preserving projections."""
import copy
from pathlib import Path

from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.acquisition.pipeline import reject_outcomes
from ForecastAgent.research_loop import POLICY, delta, delivery, gap_feedback, grounding, reading_views, post_supplement
from ForecastAgent.research_loop.fusion import POLICY_FIELD, POLICY_NAME
from ForecastAgent.research_loop.prospective_trial import prepared_pair

DONOR_COMMIT = '44532209cd4cf25e16093ea2023059bbe31d4f7e'


def enable(request):
    """Select map tools explicitly; do not change models, source limits or ledgers."""
    reject_outcomes(request)
    result = copy.deepcopy(request)
    policies = {'research_state_policy': POLICY, POLICY_FIELD: POLICY_NAME,
                grounding.FIELD: grounding.POLICY, delta.FIELD: delta.POLICY,
                delivery.FIELD: delivery.POLICY, reading_views.FIELD: reading_views.POLICY,
                gap_feedback.FIELD: gap_feedback.POLICY, post_supplement.FIELD: post_supplement.POLICY}
    for field, value in policies.items():
        if field in result and result[field] != value:
            raise ValueError('Incompatible frozen research policy: ' + field)
    result.update(policies)
    return result


def project(bundle, admitted_view, directory):
    """Check maps against originals; score selection uses only admitted originals.

    This prepares two inputs without model calls. Unavailable or rejected maps
    produce a recorded original-only fallback, never a fabricated graph.
    """
    directory = Path(directory)
    identity = {'original_bundle_sha256': digest(bundle),
                'admitted_view_sha256': digest(admitted_view), 'donor_commit': DONOR_COMMIT}
    marker = directory / 'identity.json'
    if marker.exists() and load(marker) != identity:
        raise ValueError('Frozen map projection inputs changed')
    save(marker, identity)
    if not admitted_view.get('pages'):
        report = {'status': 'needs_material_recovery', 'map_delivered': False,
                  'model_calls': 0, 'submitted': False, 'truth_verified': False}
    else:
        try:
            common, pair, audit = prepared_pair(bundle, original_view=admitted_view)
            save(directory / 'common-originals.json', common)
            for arm, value in pair.items():
                save(directory / (arm + '-input.json'), value)
            report = {'status': 'paired_inputs_prepared' if audit['map_delivered'] else 'original_only_fallback',
                      **audit, 'model_calls': 0, 'submitted': False,
                      'truth_verified': False, 'forecast_quality_verified': False}
        except (ValueError, KeyError, TypeError) as exc:
            report = {'status': 'projection_rejected', 'error': str(exc),
                      'map_delivered': False, 'model_calls': 0, 'submitted': False,
                      'direct_analysis_preserved': True, 'truth_verified': False}
    save(directory / 'report.json', report)
    return report

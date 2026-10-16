"""One shared forecast-input policy, matching the pinned market reader.

Only the two audited execution fields are omitted. Unknown platform extensions,
exact rules, source bodies, clocks and raw acquisition journals are retained.
"""
import copy
from contextlib import contextmanager

POLICY = 'market-original-scoring-boundary-v1'
LOCAL_METADATA_FIELDS = ('capability_code_identity', 'predictive_information_contract')


def project_bundle(bundle):
    from ForecastAgent.acquisition.pipeline import reject_outcomes
    reject_outcomes(bundle['request'])
    result = dict(bundle)
    result['request'] = {k: copy.deepcopy(v) for k, v in bundle['request'].items()
                         if k not in LOCAL_METADATA_FIELDS}
    return result


@contextmanager
def market_adapter():
    """Check and apply the same projection at the immutable market seam."""
    from unittest.mock import patch
    from ForecastAgent.research_loop import scoring_delivery
    if tuple(scoring_delivery.LOCAL_METADATA_FIELDS) != LOCAL_METADATA_FIELDS:
        raise ValueError('Pinned market scoring boundary differs from shared policy')
    native = scoring_delivery.prepare
    def prepare(bundle, admitted):
        projected = project_bundle(admitted)
        view, groups, audit = native(bundle, admitted)
        if view['request'] != projected['request']:
            raise ValueError('Native market forecast question differs from shared projection')
        # The final question is assigned by this same function in both hosts.
        view['request'] = projected['request']
        audit['shared_input_boundary'] = POLICY
        return view, groups, audit
    with patch.object(scoring_delivery, 'prepare', prepare):
        yield

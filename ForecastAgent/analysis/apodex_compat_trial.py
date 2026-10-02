"""Single-case budget and output compatibility pilot; no live submission."""
import argparse
from pathlib import Path

from ForecastAgent.analysis import referenced
from ForecastAgent.analysis.pilot import load
from ForecastAgent.providers.model import configured_model

PROFILES = {
    'low_effort': {'max_output_tokens': 12000, 'reasoning': {'effort': 'low'}, 'require_tool': True},
    'bounded_output': {'max_output_tokens': 6000, 'reasoning': {'enabled': False}, 'require_tool': True},
}


def run(inputs, supplements, output, ident, profile='bounded_output'):
    if configured_model() != 'apodex/apodex-1.1-mini:free':
        raise ValueError('This compatibility pilot requires Apodex free')
    root = Path(__file__).parent
    metadata = load(root / 'error_seven_time_metadata.json')
    if ident not in metadata:
        raise ValueError('Question is outside the frozen seven-case cohort')
    referenced.run(inputs, output, [ident], supplements, mode='reasoning_only',
                   audit_contract=True, question_metadata=metadata,
                   generation=PROFILES[profile], normalize_output=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--supplements', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--id', required=True)
    parser.add_argument('--profile', choices=list(PROFILES), default='bounded_output')
    args = parser.parse_args()
    run(args.inputs, args.supplements, args.output, args.id, args.profile)

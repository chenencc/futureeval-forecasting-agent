"""Post-inference diagnostics; outcome labels never enter model input packets."""
import argparse
import math
from pathlib import Path

from ForecastAgent.analysis.distributions import grid, range_metadata, nominal
from ForecastAgent.analysis.pilot import load, save, digest, WARNING
from ForecastAgent.research_loop.labels import validate as validate_label


def cdf_loss(xs, ps, truth):
    """Exact integral of squared linear-CDF error, normalized by bounded width."""
    total = 0.0
    for a, b, fa, fb in zip(xs, xs[1:], ps, ps[1:]):
        points = sorted({a, b, min(b, max(a, truth))})
        for x, y in zip(points, points[1:]):
            step = float((x+y)/2 >= truth)
            u = fa+(fb-fa)*(x-a)/(b-a)-step
            v = fa+(fb-fa)*(y-a)/(b-a)-step
            total += (y-x)*(u*u+u*v+v*v)/3
    return total/(xs[-1]-xs[0])


def score(payload, question, label):
    kind = question['question_type']
    if kind == 'binary':
        truth = label['value']
        if truth not in (0, 1):
            raise ValueError('Binary outcome must be zero or one')
        p = payload['probability_yes']
        return {'brier': (p-truth)**2, 'log_loss': -math.log(p if truth else 1-p)}
    if kind == 'multiple_choice':
        truth = label['resolution_display']; ps = payload['probability_yes_per_category']
        if truth not in ps:
            raise ValueError('Resolved option does not match frozen exact options')
        return {'multiclass_brier': sum((p-float(k == truth))**2 for k, p in ps.items()),
                'log_loss': -math.log(ps[truth])}
    xs = grid(range_metadata(question)); ps = payload['continuous_cdf']
    interval = label.get('result_kind')
    truth = xs[0] if interval == 'below_lower_bound' else xs[-1] if interval == 'above_upper_bound' else nominal(label['value'], range_metadata(question))
    if not math.isfinite(truth):
        raise ValueError('Outcome must be finite and in platform question units')
    return {'normalized_bounded_cdf_loss': cdf_loss(xs, ps, truth)}


def evaluate(root, labels_path):
    root = Path(root); report = load(root/'report.json'); labels = load(labels_path)
    rows = []
    for case in report['cases']:
        ident = case['id']; binding = labels.get(ident)
        label = binding.get('label') if isinstance(binding, dict) else None
        question = load(case['parent']['path'])['request']
        binding_audit = validate_label(question, binding)
        row = {'id': ident, 'kind': case['question_type'], 'status': case['status'],
            'label': label, 'label_binding': binding_audit,
            'evaluation_status': binding_audit['status'] if not binding_audit['eligible'] else 'not_paired'}
        if case['status'] == 'paired_completed' and binding_audit['eligible']:
            try:
                for arm in ('baseline', 'enriched'):
                    result = load(root/'cases'/ident/'decision'/(arm+'-result.json'))
                    row[arm] = score(result['payload'], question, label)
                row['difference_B_minus_A'] = {k: row['enriched'][k]-row['baseline'][k] for k in row['baseline']}
                row['evaluation_status'] = 'evaluated'
            except (ValueError, KeyError, TypeError) as exc:
                row.update(evaluation_status='label_or_metric_error', error=str(exc))
        rows.append(row)
    groups = {}
    for kind in ('binary', 'multiple_choice', 'numeric', 'discrete', 'date'):
        matched = [r for r in rows if r['kind'] == kind and r['evaluation_status'] == 'evaluated']
        groups[kind] = {'n': len(matched), 'metrics': {}}
        for key in (matched[0]['baseline'] if matched else {}):
            a = sum(r['baseline'][key] for r in matched)/len(matched)
            b = sum(r['enriched'][key] for r in matched)/len(matched)
            groups[kind]['metrics'][key] = {'A': a, 'B': b, 'B_minus_A': b-a}
    result = {'protocol': report['protocol'], 'labels_sha256': digest(labels), 'rows': rows,
        'by_type': groups, 'label_policy': 'Independently archived question/result contracts; labels and bindings never transmitted to either arm',
        'small_sample': True, 'evaluation_warning': WARNING,
        'A_cost': 'Baseline Mercury HTTP usage only',
        'B_cost': 'Super map HTTP usage plus enriched Mercury HTTP usage',
        'tail_limitation': 'Bounded CDF loss does not distinguish beyond-range outcomes',
        'all_attempts_report': str(root/'report.json')}
    save(root/'evaluation.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--labels', type=Path, required=True)
    args = p.parse_args()
    evaluate(args.root, args.labels)


if __name__ == '__main__':
    main()

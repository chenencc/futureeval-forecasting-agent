"""Bounded, resumable URL repair driven by a frozen observed-source plan."""
import argparse
import copy
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from ForecastAgent.analysis.pilot import digest, load, save, WARNING
from ForecastAgent.analysis import mercury_evidence_chain as analysis
from ForecastAgent.analysis.ensemble import metrics
from ForecastAgent.evidence.collection_handoff import LEDGERS
from ForecastAgent.supplement.stage import fetch_document, now
from ForecastAgent.readers.browser import render_page


def run(parent, baseline, output, plan_path, network=False, analyze=False):
    parent, baseline, output = Path(parent), Path(baseline), Path(output)
    plan = load(plan_path)
    inputs = {p.parent.name: p for p in parent.glob('recollection-fifty-free-repair-*/tasks/*/analysis-input.json')}
    prior = {p.parent.name: p for p in parent.glob('recollection-fifty-free-repair-*/tasks/*/repair/tasks/*/supplement.json')}
    ids = list(plan['tasks'])
    if not 1 <= len(ids) <= 6 or not set(ids) <= set(inputs) or not set(ids) <= set(prior):
        raise ValueError('One to six existing tasks and prior repair journals required')
    identity = {'protocol': 'targeted-supplement-v1', 'plan_sha256': digest(plan),
                'input_sha256': {i: digest(load(inputs[i])) for i in ids},
                'prior_journal_sha256': {i: digest(load(prior[i])) for i in ids},
                'additional_caps': {'http': 6, 'browser': 2},
                'network': network, 'analyze': analyze, 'budget_reset': False,
                'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'retained_analysis_sha256': hashlib.sha256(Path(analysis.__file__).read_bytes()).hexdigest()}
    if (output/'manifest.json').exists() and load(output/'manifest.json') != identity:
        raise ValueError('Frozen targeted repair identity changed')
    save(output/'manifest.json', identity)
    rows = []
    for ident in ids:
        task = output/'tasks'/ident
        bundle = load(inputs[ident]); original = copy.deepcopy(bundle)
        if analyze:
            old_inputs = list(baseline.glob('repaired-fifty-analysis-*/tasks/'+ident+'/analysis-input.json'))
            if len(old_inputs) != 1 or digest(load(old_inputs[0])) != digest(original):
                raise ValueError('Frozen baseline and repair parent differ')
        old = load(prior[ident]); save(task/'prior-supplement.json', old)
        state_path = task/'repair-state.json'
        state = load(state_path) if state_path.exists() else {'attempts': [], 'captures': []}
        for item in plan['tasks'][ident]['sources']:
            url = item['url']
            if urlsplit(url).hostname in {'metaculus.com', 'www.metaculus.com'}:
                raise ValueError('Outcome platform cannot be a research source')
            modes = ['browser'] if item.get('browser_only') else ['http', 'browser']
            for mode in modes:
                if any(a['url'] == url and a['tool'] == mode for a in state['attempts']):
                    continue
                if any(c['url'] == url for c in state['captures']):
                    break
                if sum(a['tool'] == mode for a in state['attempts']) >= identity['additional_caps'][mode]:
                    break
                if not network:
                    break
                entry = {'url': url, 'tool': mode, 'status': 'reserved', 'started_at_utc': now(),
                         'source_plan_item_sha256': digest(item)}
                state['attempts'].append(entry); save(state_path, state)
                try:
                    page = fetch_document(url) if mode == 'http' else render_page(url, retrieved_at=now())
                    filename = f'captures/{len(state["attempts"]):03}.json'
                    save(task/filename, page)
                    entry['response_file'] = filename
                    entry['body_diagnostics'] = page.get('body_diagnostics', {})
                    if not page.get('body_diagnostics', {}).get('usable_text'):
                        raise ValueError('Captured body is empty, blocked or unusable')
                    state['captures'].append({'url': url, 'file': filename, 'sha256': digest(page)})
                    entry.update(status='captured', body_chars=len(page['content']))
                except Exception as exc:
                    entry.update(status='failed', error=str(exc)[:400], audit=getattr(exc, 'audit', None))
                entry['completed_at_utc'] = now(); save(state_path, state)
        added = []
        for capture in state['captures']:
            page = load(task/capture['file'])
            if digest(page) != capture['sha256']:
                raise ValueError('Saved capture changed')
            key = capture['url']
            if key in bundle['pages']:
                if bundle['pages'][key].get('content') == page['content']:
                    continue
                key += ('&' if '#' in key else '#') + 'capture=' + capture['sha256'][:12]
            page['supplement_provenance'] = {'protocol': identity['protocol'],
                'actual_requested_url': capture['url'], 'parent_bundle_sha256': digest(original)}
            bundle['pages'][key] = page; added.append(key)
        if any(digest(bundle.get(k, [])) != digest(original.get(k, [])) for k in LEDGERS):
            raise ValueError('Original provider ledger changed')
        save(task/'analysis-input.json', bundle)
        row = {'id': ident, 'added_pages': added, 'new_attempts': state['attempts'],
               'prior_attempts': len(old['attempts']), 'existing_ledgers_preserved': True,
               'unfetched_plan_urls': [s['url'] for s in plan['tasks'][ident]['sources']
                                      if s['url'] not in {c['url'] for c in state['captures']}],
               'semantic_gaps_not_automatically_closed': True}
        if analyze:
            try:
                row['analysis'] = analysis.run_task(bundle, task/'mercury')
            except Exception as exc:
                row['analysis'] = {'status': 'failed', 'error': str(exc)}
        save(task/'outcome.json', row); rows.append(row)
        save(output/'report.json', {'rows': rows, 'tavily_calls': 0, 'exa_calls': 0,
             'retrieval_agent_calls': 0, 'forecast_submissions': 0, 'evaluation_warning': WARNING})
        print(ident, 'added_pages', len(added), flush=True)


def evaluate(baseline, output, labels):
    output = Path(output); report = load(output/'report.json')
    # Freeze provider outputs before reading held-out labels.
    frozen = digest(report)
    results = {r['id']: r for r in load(labels)['labels']}
    rows = []
    for row in report['rows']:
        ident = row['id']; result = row.get('analysis', {})
        if result.get('status') != 'completed':
            continue
        stage = 'second' if result['second_call_required'] else 'first'
        response = analysis.decisions.validate(load(output/'tasks'/ident/'mercury'/stage/'response.json'), analysis.questions())
        if result['probability_yes'] != response['answers']['event_yes']['noul']:
            raise ValueError('Scored probability changed')
        old = list(Path(baseline).glob('repaired-fifty-analysis-*/tasks/'+ident+'/outcome.json'))
        if len(old) != 1:
            raise ValueError('Unique retained baseline required')
        p0 = load(old[0])['clipped_probability_yes']; p1 = result['clipped_probability_yes']; y = results[ident]['resolved_to']
        rows.append({'id': ident, 'old_probability_yes': p0, 'new_probability_yes': p1,
                     'resolution': y, 'old_metrics': metrics(p0, y), 'new_metrics': metrics(p1, y),
                     'added_pages': len(row['added_pages'])})
    save(output/'metrics.json', {'rows': rows, 'n': len(rows), 'frozen_output_sha256_before_labels': frozen,
        'same_retained_analysis_chain_new_evidence': True, 'outcome_selected_repair_diagnostic': True,
        'evaluation_warning': WARNING, 'automatic_use_eligible': False,
        'aggregate': {route: {k: sum(r[route][k] for r in rows)/len(rows)
                      for k in ('brier', 'log_loss', 'correct_at_half')} for route in ('old_metrics', 'new_metrics')} if rows else {}})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['run', 'evaluate'])
    for name in ('parent', 'baseline', 'output', 'plan', 'labels'):
        parser.add_argument('--'+name)
    parser.add_argument('--network', action='store_true')
    parser.add_argument('--analyze', action='store_true')
    args = parser.parse_args()
    if args.command == 'run':
        run(args.parent, args.baseline, args.output, args.plan, args.network, args.analyze)
    else:
        evaluate(args.baseline, args.output, args.labels)

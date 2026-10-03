"""Extend free repair for the completed fifty; never resume the pending seventy."""
import argparse
import json
import shutil
from pathlib import Path

from ForecastAgent.evidence.collection_handoff import LEDGERS, analysis_view
from ForecastAgent.supplement.stage import run, analysis_overlay, digest, save

REASON = 'User authorized additional free crawling of the completed fifty; preserve prior attempts.'


def execute(parent, output, batch, *, network=False):
    parent, output = Path(parent), Path(output)
    ids = sorted(p.name for p in (parent / 'handoffs').iterdir()
                 if p.is_dir() and (p / 'analysis-input.json').exists())
    if len(ids) != 50 or not all(i.isdecimal() for i in ids):
        raise ValueError('Expected exactly fifty completed handoffs')
    if not 0 <= batch < 10:
        raise ValueError('Select one of ten five-task batches')
    selected = ids[batch * 5:(batch + 1) * 5]
    identity = {'protocol':'recollection-fifty-free-repair-v1', 'all_ids':ids,
                'batch':batch, 'selected_ids':selected, 'network':network,
                'parent_inputs_sha256':{
                    i:digest(json.loads((parent / 'handoffs' / i / 'repair-input.json').read_text(encoding='utf-8')))
                    for i in selected},
                'caps':{'http_total':10, 'browser_total':4, 'local_reparse_total':8}}
    manifest = output / 'extension-manifest.json'
    if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8')) != identity:
        raise ValueError('Frozen extension input changed')
    save(manifest, identity)
    reports = []
    for ident in selected:
        source = parent / 'handoffs' / ident
        destination = output / 'tasks' / ident
        repair = destination / 'repair'
        if not repair.exists():
            # Preserve every prior successful, failed and interrupted reservation.
            shutil.copytree(source / 'repair', repair)
        view = json.loads((source / 'repair-input.json').read_text(encoding='utf-8'))
        old = json.loads((source / 'repair/tasks' / ident / 'supplement.json').read_text(encoding='utf-8'))
        run(source / 'repair-parent.zip', repair, [ident], network=network,
            http_limit=10, browser_limit=4, budget_extension_reason=REASON)
        child = json.loads((repair / 'tasks' / ident / 'supplement.json').read_text(encoding='utf-8'))
        if child['attempts'][:len(old['attempts'])] != old['attempts']:
            raise ValueError('Original supplement reservations changed')
        overlay = analysis_overlay(view, repair, ident)
        if any(digest(overlay.get(k, [])) != digest(view.get(k, [])) for k in LEDGERS):
            raise ValueError('Original acquisition ledger changed')
        overlay['supplement_lineage']['remaining_gaps'] = child['remaining_gaps']
        raw = json.loads((source / 'raw-bundle.json').read_text(encoding='utf-8'))
        result = analysis_view(overlay, digest(raw))
        save(destination / 'analysis-input.json', result)
        before = json.loads((source / 'analysis-input.json').read_text(encoding='utf-8'))
        added = {u:p for u,p in result['pages'].items() if u not in before['pages']}
        report = {'task_id':ident, 'before_pages':len(before['pages']),
                  'after_pages':len(result['pages']), 'added_pages':list(added),
                  'added_characters':sum(len(p.get('content','')) for p in added.values()),
                  'original_gap_count':len(old.get('remaining_gaps',[])),
                  'remaining_gaps':child['remaining_gaps'],
                  'prior_attempts':len(old['attempts']), 'total_attempts':len(child['attempts']),
                  'new_attempts':child['attempts'][len(old['attempts']):],
                  'ledgers_preserved':True, 'relevance_verified':False,
                  'time_eligibility_verified':False, 'original_status_unchanged':True}
        save(destination / 'report.json', report)
        reports.append(report)
        save(output / 'report.json', {'protocol':identity['protocol'], 'selected_ids':selected,
             'tasks':reports, 'provider_calls':{'models':0,'tavily':0,'exa':0},
             'forecast_submissions':False, 'pending_seventy_untouched':True})
    return reports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--batch', type=int, required=True)
    parser.add_argument('--network', action='store_true')
    args = parser.parse_args()
    reports = execute(args.parent,args.output,args.batch,network=args.network)
    print(json.dumps({'tasks':len(reports), 'additional_pages':sum(len(r['added_pages']) for r in reports)},indent=2))


if __name__ == '__main__':
    main()

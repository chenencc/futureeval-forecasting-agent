"""Offline research sidecars. Existing files are never overwritten."""
import argparse
import json
from pathlib import Path
from ForecastAgent.market_pulse import consensus, research_inventory, mechanism


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import-consensus')
    imp.add_argument('--source', required=True)
    imp.add_argument('--mapping', required=True)
    imp.add_argument('--format', choices=['html', 'grid-json'], required=True)
    imp.add_argument('--table-index', type=int, default=0)
    imp.add_argument('--output', required=True)
    inv = sub.add_parser('inventory')
    inv.add_argument('--package', required=True)
    inv.add_argument('--variables', required=True)
    inv.add_argument('--state', help='Archived model state containing original_evidence_library')
    inv.add_argument('--consensus', action='append', default=[])
    inv.add_argument('--as-of', help='Explicit timestamp with UTC offset')
    inv.add_argument('--output', required=True)
    mech = sub.add_parser('plan-mechanisms')
    mech.add_argument('--package', required=True)
    mech.add_argument('--variables', required=True)
    mech.add_argument('--state')
    mech.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'import-consensus':
        config = load(args.mapping)
        raw = None
        gaps = []
        if args.format == 'html':
            raw = Path(args.source).read_text(encoding='utf-8')
            tables, gaps = consensus.html_tables(raw)
            selected = next((t for t in tables if t['table_index'] == args.table_index), None)
            if selected is None:
                raise ValueError('Selected table unavailable: ' + json.dumps(gaps))
            rows = selected['rows']
        else:
            rows = load(args.source)
            if isinstance(rows, dict):
                raise ValueError('Provider response or error envelope is not an explicit grid')
        result = consensus.import_grid(rows, config['metadata'], config['mappings'], raw_source=raw)
        result['table_diagnostics'] = gaps
        consensus.validate(result)
    else:
        state = load(args.state) if args.state else None
        if state is not None and 'original_evidence_library' not in state:
            raise ValueError('Archived state has no recorded exposure library')
        if args.command == 'plan-mechanisms':
            result = mechanism.plan(load(args.package), load(args.variables),
                library=state['original_evidence_library'] if state else None)
            result['provider_calls'] = 0
        else:
            result = research_inventory.audit(load(args.package), load(args.variables),
                library=state['original_evidence_library'] if state else None,
                consensus_packages=[load(p) for p in args.consensus], as_of=args.as_of)
    save(args.output, result)
    print(json.dumps({'output': str(Path(args.output).resolve()), 'schema': result['schema'],
        'provider_calls': result['provider_calls'], 'records': len(result.get('records', [])),
        'states': result.get('state_distribution', {})}))


if __name__ == '__main__':
    main()

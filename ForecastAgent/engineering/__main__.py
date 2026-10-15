"""Local engineering commands. No CLI command invokes models, search or delivery."""
import argparse
import json
from pathlib import Path

from .common import connect, now, read, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='Independent engineering store')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    sub.add_parser('status')
    catalog = sub.add_parser('inventory')
    catalog.add_argument('--domain', choices=['competition', 'market'], required=True)
    catalog.add_argument('--database', required=True)
    export = sub.add_parser('freeze')
    export.add_argument('--spec', required=True)
    replay = sub.add_parser('replay')
    replay.add_argument('--experiment', required=True)
    observe = sub.add_parser('observe')
    observe.add_argument('--domain', choices=['competition', 'market'], required=True)
    observe.add_argument('--database', required=True)
    evaluate = sub.add_parser('evaluate')
    evaluate.add_argument('--experiment', required=True)
    args = parser.parse_args()
    db = connect(args.root)
    try:
        if args.command == 'doctor':
            import importlib.metadata
            from .contracts import validate
            from .experiments import implementation
            result = {'schema': 'forecast-engineering-doctor-v1', 'sqlite_schema_version': db.execute('PRAGMA user_version').fetchone()[0],
                      'jsonschema': importlib.metadata.version('jsonschema'), 'implementation_sha256': implementation(),
                      'database_integrity': db.execute('PRAGMA integrity_check').fetchone()[0],
                      'provider_calls': 0, 'production_writes': 0}
        elif args.command == 'inventory':
            from .snapshots import inventory
            result = {'batches': inventory(args.domain, args.database), 'selection_is_not_frozen': True}
        elif args.command == 'freeze':
            from .snapshots import packet
            from .experiments import freeze, manifest
            spec = read(args.spec)
            packets = [packet(c['domain'], c['database'], c['batch_id'], c['task_key'], as_of_utc=c.get('as_of_utc')) for c in spec['cases']]
            ident = freeze(db, spec['name'], packets, spec['routes'])
            result = {'experiment_id': ident, 'manifest': manifest(db, ident)}
            write(Path(args.root) / 'manifests' / (ident + '.json'), result)
        elif args.command == 'replay':
            from .experiments import manifest, run
            frozen = manifest(db, args.experiment)
            rows = [run(db, args.experiment, c['id'], r['id'], audit_only=True) for c in frozen['cases'] for r in frozen['routes']]
            result = {'experiment_id': args.experiment, 'runs': rows, 'mode': 'offline_input_validation',
                      'forecast_generated': False, 'provider_calls': 0}
        elif args.command == 'observe':
            from .native import sync
            result = sync(db, args.domain, args.database)
        elif args.command == 'evaluate':
            from .experiments import evaluate
            result = evaluate(db, args.experiment)
        else:
            from .trace import summary
            result = summary(db)
            result['experiments'] = [dict(r) for r in db.execute('SELECT id,name,created_at FROM experiments')]
            result['lifecycle_events'] = db.execute('SELECT count(*) FROM lifecycle').fetchone()[0]
            result['lifecycle_distribution'] = [dict(r) for r in db.execute(
                'SELECT domain,stage,state,count(*) AS tasks,max(occurred_at) AS last_observed_at FROM lifecycle e '
                'WHERE e.rowid=(SELECT x.rowid FROM lifecycle x WHERE x.domain=e.domain AND x.task_key=e.task_key '
                'AND x.stage=e.stage ORDER BY x.occurred_at DESC,x.rowid DESC LIMIT 1) GROUP BY domain,stage,state')]
        write(Path(args.root) / ('last-' + args.command + '.json'), result)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        db.close()


if __name__ == '__main__':
    main()

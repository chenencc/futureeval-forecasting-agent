"""Read-only local acceptance of both durable pipeline projections.

This checks ingestion and delivery invariants, not evidence truth or accuracy.
No API credentials, network clients, research calls or forecast submissions.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from ForecastAgent.local_transfer import atomic_json


def distribution(db, sql):
    return {str(row[0]):row[1] for row in db.execute(sql)}


def inspect_database(root, domain):
    root=Path(root)
    path=root/('market.sqlite3' if domain=='market' else 'competition.sqlite3')
    result={'database':str(path),'errors':[],'gaps':[]}
    if not path.is_file():
        return {**result,'status':'failed','errors':['database_missing']}
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=30)
    db.row_factory=sqlite3.Row
    try:
        result['integrity']=db.execute('PRAGMA quick_check').fetchone()[0]
        result['foreign_key_errors']=[list(r) for r in db.execute('PRAGMA foreign_key_check')]
        if result['integrity']!='ok' or result['foreign_key_errors']:
            result['errors'].append('database_integrity_failed')
        receipt=root/'latest-sync.json'
        sync=json.loads(receipt.read_bytes()) if receipt.is_file() else {}
        result['sync']={k:sync.get(k) for k in ('status','started_at','finished_at','reason')}
        sync_status=sync.get('market_status',sync.get('status')) if domain=='market' else sync.get('status')
        if sync_status!='ok': result['errors'].append('last_domain_sync_not_successful')
        finished=sync.get('finished_at')
        if not finished or (datetime.now(timezone.utc)-datetime.fromisoformat(finished.replace('Z','+00:00'))).total_seconds()>5400:
            result['errors'].append('local_sync_missing_or_older_than_90_minutes')
        if domain=='market':
            result['admitted']=db.execute('SELECT count(*) FROM markets').fetchone()[0]
            result['worker_states']=distribution(db,'SELECT status,count(*) FROM latest_tasks GROUP BY status')
            result['discovery_states']=distribution(db,'SELECT status,count(*) FROM latest_discovery GROUP BY status')
            result['awaiting_worker_checkpoint']=[r[0] for r in db.execute(
                'SELECT m.key FROM markets m LEFT JOIN latest_tasks t ON t.market_key=m.key WHERE t.market_key IS NULL')]
            result['completed_without_original_score']=[r[0] for r in db.execute(
                "SELECT t.market_key FROM latest_tasks t WHERE t.status='completed' AND NOT EXISTS ("
                "SELECT 1 FROM forecasts f WHERE f.market_key=t.market_key AND f.arm='original' "
                "AND f.status='completed' AND f.probability_yes IS NOT NULL)")]
            if result['completed_without_original_score']:result['errors'].append('completed_task_missing_score')
            result['material_reference_availability']=distribution(db,'SELECT availability,count(*) FROM material_refs GROUP BY availability')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name='latest_web_packets'").fetchone():
                result['local_material_packets']=distribution(db,
                    'SELECT material_status,count(*) FROM latest_web_packets GROUP BY material_status')
            result['import_heads']=[dict(r) for r in db.execute('SELECT * FROM imports ORDER BY imported_at DESC LIMIT 6')]
            if result['awaiting_worker_checkpoint']:result['gaps'].append('admitted_tasks_awaiting_worker_checkpoint')
            if result['material_reference_availability'].get('remote_archive_reference'):
                result['gaps'].append('remote_references_do_not_prove_local_body_import')
        else:
            result['questions']=db.execute('SELECT count(*) FROM questions').fetchone()[0]
            result['worker_states']=distribution(db,'SELECT stage,count(*) FROM latest_tasks GROUP BY stage')
            result['delivery_states']=distribution(db,'SELECT status,count(*) FROM latest_receipts GROUP BY status')
            result['accepted_without_bound_receipt']=[r[0] for r in db.execute(
                "SELECT t.question_key FROM latest_tasks t LEFT JOIN latest_receipts r ON r.question_key=t.question_key "
                "WHERE t.stage='accepted' AND (r.status IS NULL OR r.status!='accepted' OR r.verified_binding!=1)")]
            row=db.execute("SELECT value FROM metadata WHERE key='legacy_unbound_receipts'").fetchone()
            legacy=set(json.loads(row[0])) if row else set()
            result['new_accepted_without_bound_receipt']=[r[0] for r in db.execute(
                "SELECT t.question_key,r.id FROM latest_tasks t LEFT JOIN latest_receipts r ON r.question_key=t.question_key "
                "WHERE t.stage='accepted' AND (r.status IS NULL OR r.status!='accepted' OR r.verified_binding!=1)")
                if r[1] not in legacy]
            if result['new_accepted_without_bound_receipt']:result['errors'].append('new_accepted_task_missing_bound_receipt')
            # Historical receipts may predate the saved candidate. Keep this a
            # visible audit gap; do not fabricate bindings or resubmit questions.
            if set(result['accepted_without_bound_receipt'])-set(result['new_accepted_without_bound_receipt']):
                result['gaps'].append('historical_accepted_receipt_not_bound')
            result['invalid_accepted_receipts']=[r[0] for r in db.execute(
                "SELECT r.id FROM receipts r LEFT JOIN forecasts f ON f.id=r.forecast_id WHERE r.status='accepted' "
                "AND (r.verified_binding!=1 OR r.confirmed_at IS NULL OR f.id IS NULL "
                "OR r.question_key!=f.question_key OR r.payload!=f.payload)")]
            if result['invalid_accepted_receipts']:result['errors'].append('accepted_receipt_payload_mismatch')
            result['material_reference_availability']=distribution(db,'SELECT availability,count(*) FROM file_refs GROUP BY availability')
            result['model_input_bindings']=distribution(db,'SELECT status,count(*) FROM model_inputs GROUP BY status')
            result['import_heads']=[dict(r) for r in db.execute(
                'SELECT stream,run_id,observed_at,imported_at FROM imports WHERE run_id IS NOT NULL ORDER BY imported_at DESC LIMIT 4')]
            if result['material_reference_availability'].get('missing'):result['gaps'].append('historical_native_objects_missing')
        result['status']='failed' if result['errors'] else 'ok_with_gaps' if result['gaps'] else 'ok'
    except (sqlite3.Error,ValueError,TypeError) as exc:
        result.update(status='failed',errors=[*result['errors'],type(exc).__name__+': '+str(exc)[:200]])
    finally:
        db.close()
    return result


def audit(root):
    root=Path(root)
    domains={name:inspect_database(root/path,name) for name,path in (
        ('market','prediction_markets/store'),('competition','competitions/store'))}
    status='failed' if any(x['status']=='failed' for x in domains.values()) else (
        'ok_with_gaps' if any(x['status']=='ok_with_gaps' for x in domains.values()) else 'ok')
    return {'schema':'forecast-pipeline-operations-v1','observed_at':datetime.now(timezone.utc).isoformat(),
            'status':status,'domains':domains,'scope':'Local projection consistency, not global discovery recall or forecast accuracy',
            'model_requests':0,'search_requests':0,'submissions':0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='E:/metaculus_data')
    parser.add_argument('--output')
    args=parser.parse_args()
    result=audit(args.root)
    if args.output:atomic_json(args.output,result)
    print(json.dumps(result,ensure_ascii=False))
    if result['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()

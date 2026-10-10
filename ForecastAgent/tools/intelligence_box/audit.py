"""Replay preserved captures offline and emit a compact structural audit."""
import argparse
import json
from pathlib import Path
from .catalog import SOURCES
from .core import parse, digest, now, DocumentRejected


def audit(root):
    rows=[]
    for path in sorted(Path(root).glob("captures/*/capture.json")):
        capture=json.loads(path.read_text(encoding="utf-8"))
        raw=(Path(root)/capture["raw_path"]).read_bytes()
        source_id=capture["source_id"]
        source=SOURCES[source_id] if source_id!="document" else {"format":"document","url":capture["request_url"],"final_url":capture["http"]["final_url"],"content_type":capture["http"]["content_type"],"captured_at":capture["captured_at_utc"]}
        row={"id":capture["id"],"source":source_id,"url":capture["request_url"],"captured_at_utc":capture["captured_at_utc"],"http_status":capture["http"]["status"],"hash_matches":digest(raw)==capture["raw_sha256"],"original_state":capture["status"],"checks":{}}
        try:
            records,coverage=parse(source,raw)
            row.update(replay_state="usable" if records else "empty",record_count=len(records),more_available=coverage["more_available"])
            if source_id=="worldbank":
                row["checks"]={"exact_country":all(r["country"]["id"]==capture["parameters"]["country"] for r in records),"exact_indicator":all(r["indicator"]["id"]==capture["parameters"]["indicator"] for r in records),"observation_years":[r["date"] for r in records],"explicit_unit_field_nonempty":all(bool(r.get("unit")) for r in records)}
            if source_id=="fred_csv": row["checks"]={"series_column_present":all(capture["parameters"]["id"] in r for r in records),"dates":[r.get("observation_date",r.get("DATE")) for r in records],"unit_metadata_available":False}
            if source_id=="uk_bills": row["checks"]={"stage_preserved":all("currentStage" in r and "isAct" in r for r in records),"first_stage":records[0]["currentStage"]["description"] if records else None}
            if source_id=="federal_register": row["checks"]={"document_type_preserved":all("type" in r for r in records),"official_pdf_links":sum(bool(r.get("pdf_url")) for r in records),"precision_warning":"Full-text keyword hits include incidental mentions; title relevance is not guaranteed"}
            if source_id=="clinical_trials": row["checks"]={"ids":[r["protocolSection"]["identificationModule"]["nctId"] for r in records],"data_update_dates":[r["protocolSection"]["statusModule"].get("lastUpdatePostDateStruct") for r in records]}
            if source_id=="crossref": row["checks"]={"types":[r.get("type") for r in records],"full_text_obtained":False}
            if source_id=="document": row["checks"]={"body_diagnostics":coverage["native_metadata"].get("body_diagnostics"),"pdf_table_extraction_unavailable":any(r.get("metadata",{}).get("table_extraction")=="unavailable" for r in records),"chars":sum(len(r.get("page_content","")) for r in records)}
        except Exception as exc:
            row.update(replay_state="failed",error_type=type(exc).__name__)
            if isinstance(exc,DocumentRejected): row["checks"]["body_diagnostics"]=exc.diagnostics
        rows.append(row)
    return {"version":"toolbox_audit_v1","audited_at_utc":now(),"network_requests_during_audit":0,"captures":rows,"all_raw_hashes_match":all(r["hash_matches"] for r in rows),"state_distribution":{s:sum(r["replay_state"]==s for r in rows) for s in ("usable","empty","failed")},"truth_verification_performed":False,"production_integration_complete":False}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    report=audit(args.root)
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="captures"}))


if __name__=="__main__": main()

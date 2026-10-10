"""Re-read a preserved issuer PDF under its originally declared task byte cap."""
import argparse
import json
from pathlib import Path
from .core import Toolbox,parse,now
from .profiles import PROFILES


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True)
    args=p.parse_args()
    root=Path(args.root)
    follow=json.loads((root/"priority-followup.json").read_text(encoding="utf-8"))
    original=next(c for c in follow["cases"] if c["label"].startswith("Tesla Q2"))
    box=Toolbox(root,max_requests=25,max_document_bytes=16_000_000)
    try:
        capture,raw=box._saved(original["capture_id"])
        source={"format":"document","url":capture["request_url"],"final_url":capture["http"]["final_url"],"content_type":capture["http"]["content_type"],"captured_at":capture["captured_at_utc"],"decoded_byte_cap":capture["byte_cap"],"response_headers":capture["http"].get("response_headers",{})}
        records,coverage=parse(source,raw)
        source_pdf=root/"tesla-q2-2026.pdf"
        source_pdf.write_bytes(raw)
        document={"schema":"saved_document_replay_v1","replayed_at_utc":now(),"source_capture_id":capture["id"],"raw_sha256":capture["raw_sha256"],"original_status":capture["status"],"replay_status":"usable" if records else "empty","original_capture_unchanged":True,"original_capture_time":capture["captured_at_utc"],"http_requests":0,"model_requests":0,"records":records,"coverage":coverage,"budget_unchanged":box.budget(),"financial_pages":[d["metadata"].get("page") for d in records if any(s in d["page_content"].lower() for s in ("financial summary","total revenues","earnings per share"))]}
        (root/"tesla-replay.json").write_text(json.dumps(document,indent=2,ensure_ascii=False),encoding="utf-8")
        print(json.dumps({k:v for k,v in document.items() if k not in {"records","coverage"}},ensure_ascii=True),flush=True)
    finally: box.close()


if __name__=="__main__": main()

"""Verify discovery-to-body reading and exact trial identity without new search."""
import argparse
import json
from pathlib import Path
from .core import Toolbox, now


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True)
    root=Path(p.parse_args().root)
    initial=json.loads((root/"report.json").read_text(encoding="utf-8"))
    box=Toolbox(root,max_requests=30)
    results=[]
    try:
        results.append(box.fetch("clinical_trials",{"query.id":"NCT04283461"}))
        for case in initial["cases"]:
            if case["source"]=="fed_news":
                results.append(box.read(case["example"]["link"]))
            if case["source"]=="federal_register":
                results.append(box.read(case["example"]["html_url"]))
                results.append(box.read(case["example"]["pdf_url"]))
        summary={"checked_at_utc":now(),"results":[{"source":r["source_id"],"url":r["request_url"],"status":r["status"],"records":len(r["records"]),"quality":r["quality"],"error":r.get("error"),"capture_id":r["id"],"raw_bytes":r.get("raw_bytes"),"example":r["records"][0] if r["records"] else None} for r in results],"budget":box.budget()}
        (root/"followup.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
        print(json.dumps({"statuses":[r["status"] for r in results],"budget":box.budget()}),flush=True)
    finally: box.close()


if __name__=="__main__": main()

"""A bounded public-source pilot. No model, search-provider or submission calls."""
import argparse
import json
from pathlib import Path
from .core import Toolbox, now

CASES=[
    ("worldbank",{"country":"US","indicator":"NY.GDP.MKTP.KD.ZG","date":"2020:2024"},"GDP growth series; preserve country, indicator, observation year and nulls"),
    ("fred_csv",{"id":"DGS10","cosd":"2026-09-01","coed":"2026-09-07"},"US ten-year treasury yield with observation dates; latest export is not a vintage"),
    ("fed_news",{},"Federal Reserve announcement leads; follow original full-text links"),
    ("federal_register",{"conditions[term]":"artificial intelligence"},"Regulatory document leads, type and effective-date context"),
    ("uk_bills",{"SearchTerm":"finance"},"Legislation records, not proof of enactment"),
    ("clinical_trials",{"query.term":"NCT05413049"},"Exact trial identity and registry stage, not efficacy verification"),
    ("usgs",{"starttime":"2026-09-01","endtime":"2026-09-08","minmagnitude":5},"Earthquake geometry, magnitude and event time"),
    ("nasa_eonet",{},"Natural event lead records and underlying source URLs"),
    ("crossref",{"query":"probabilistic forecasting"},"Bibliographic discovery, not full papers"),
]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",required=True)
    args=parser.parse_args()
    root=Path(args.root)
    box=Toolbox(root,max_requests=30)
    report={"version":"toolbox_public_pilot_v1","started_at_utc":now(),"scope":"Availability and structure smoke test; not forecasting quality or factual verification", "cases":[]}
    try:
        for source,params,expectation in CASES:
            result=box.fetch(source,params)
            row={"source":source,"status":result["status"],"capture_id":result["id"],"url":result["request_url"],"records":len(result["records"]),"http":result.get("http"),"quality":result["quality"],"coverage":result.get("coverage"),"limitations":result["limitations"],"error":result.get("error"),"expected_use":expectation}
            if result["records"]: row["example"]=result["records"][0]
            report["cases"].append(row)
            print(json.dumps({k:row[k] for k in ("source","status","records","error")}),flush=True)
            (root/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        report["budget"]=box.budget()
        report["finished_at_utc"]=now()
        (root/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    finally: box.close()


if __name__=="__main__": main()

"""Bounded detail-page recovery and offline table/provision inspections."""
import argparse
import json
from pathlib import Path
from urllib.parse import urljoin
from .core import Toolbox,now


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True)
    root=Path(p.parse_args().root)
    previous=json.loads((root/"priority-report.json").read_text(encoding="utf-8"))
    box=Toolbox(root,max_requests=25,max_document_bytes=16_000_000)
    report={"checked_at_utc":now(),"cases":[],"saved_material_inspections":[],"analysis_started":False,"submitted":False}
    def save(label,result):
        report["cases"].append({"label":label,"capture_id":result.get("id"),"source_id":result["source_id"],"url":result.get("request_url"),"status":result["status"],"records":len(result["records"]),"raw_bytes":result.get("raw_bytes"),"http":result.get("http"),"error":result.get("error"),"quality":result.get("quality"),"limitations":result.get("limitations")})
        report["budget"]=box.budget()
        (root/"priority-followup.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
        print(json.dumps({"label":label,"status":result["status"],"records":len(result["records"])}),flush=True)
        return result
    try:
        # The exact PDF link was observed on the issuer's official IR page,
        # independently of the direct index request that returned HTTP 403.
        report["tesla_pdf_discovery"]={"source":"https://ir.tesla.com/","observation":"Official Q2 2026 Shareholder Deck link inspected via web before this pilot", "method":"explicit previously observed link; not a guessed URL or automatic new search"}
        save("Tesla Q2 2026 report via observed issuer link",box.profile("tesla_ir","https://assets-ir.tesla.com/tesla-contents/IR/TSLA-Q2-2026-Update.pdf"))
        report["bill_identity_discovery"]={"source":"v0.1 pilot saved UK Bills API response","bill_id":4038,"method":"reuse exact saved bill identity after the fresh list call timed out"}
        save("UK bill 4038 detail from saved identity",box.fetch("uk_bill_detail",{"bill_id":4038}))
        publications=save("UK bill 4038 publication index",box.fetch("uk_bill_publications",{"bill_id":4038}))
        if publications["records"]:
            pub=publications["records"][0]
            documents=pub.get("documents",[])
            if documents:
                doc=documents[0]
                pub_id=pub.get("id",pub.get("publicationId"))
                doc_id=doc.get("id",doc.get("documentId"))
                if pub_id and doc_id:
                    download=save("UK bill publication download",box.read(f"https://bills-api.parliament.uk/api/v1/Publications/{pub_id}/Documents/{doc_id}/Download"))
                    if download.get("http",{}).get("status") in {301,302,303,307,308} and download["http"].get("redirect_location"):
                        save("UK publication explicit redirect follow",box.read(urljoin(download["request_url"],download["http"]["redirect_location"])))
        for case in previous["cases"]:
            if case["label"]=="Microsoft FY2026 Q4 earnings" and case["status"]=="usable":
                table_result=box.tables(case["capture_id"],1000)
                (root/"microsoft-tables.json").write_text(json.dumps(table_result,indent=2,ensure_ascii=False),encoding="utf-8")
                report["saved_material_inspections"].append({"label":"Microsoft raw HTML tables","capture_id":case["capture_id"],"table_count":len(table_result["tables"]),"rows":sum(len(t["rows"]) for t in table_result["tables"]),"network_requests":0})
            if case["label"]=="UK Data Use and Access Act as enacted" and case["status"]=="usable":
                result=box.provisions(case["capture_id"],"section-138")
                (root/"uk-section-138.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
                report["saved_material_inspections"].append({"label":"Exact final numbered section beyond initial 500 excerpts","capture_id":case["capture_id"],"provision_id":"section-138","status":result["status"],"text_chars":len(result.get("text","")),"network_requests":0})
        report["finished_at_utc"]=now()
        report["budget"]=box.budget()
        (root/"priority-followup.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    finally: box.close()


if __name__=="__main__": main()

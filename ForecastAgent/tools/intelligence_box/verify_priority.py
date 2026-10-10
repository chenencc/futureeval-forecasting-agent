"""Bounded issuer, election-authority and legislation detail pilot."""
import argparse
import json
from pathlib import Path
from .core import Toolbox,now


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True)
    root=Path(p.parse_args().root)
    root.mkdir(parents=True,exist_ok=True)
    box=Toolbox(root,max_requests=25,max_document_bytes=16_000_000)
    report={"version":"priority_domains_pilot_v1","started_at_utc":now(),"cases":[],"analysis_started":False,"submitted":False,"model_requests":0,"search_requests":0}
    def save(label,result):
        row={"label":label,"source_id":result["source_id"],"status":result["status"],"capture_id":result.get("id"),"url":result.get("request_url"),"records":len(result["records"]),"raw_bytes":result.get("raw_bytes"),"error":result.get("error"),"configuration_names":result.get("configuration_names"),"quality":result.get("quality"),"limitations":result.get("limitations"),"source_binding":result.get("source_binding")}
        report["cases"].append(row)
        report["budget"]=box.budget()
        (root/"priority-report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
        print(json.dumps({k:row[k] for k in ("label","status","records","error")}),flush=True)
        return result
    try:
        tesla=save("Tesla disclosure index",box.profile("tesla_ir"))
        if tesla["status"]=="usable":
            links=box.links(tesla["id"],100)
            (root/"tesla-links.json").write_text(json.dumps(links,indent=2),encoding="utf-8")
            candidates=[l for l in links["links"] if "q2-2026" in l["url"].lower() and l["url"].lower().endswith(".pdf")]
            if candidates: save("Tesla Q2 2026 shareholder report",box.profile("tesla_ir",candidates[0]["url"]))
        save("Microsoft FY2026 Q4 earnings",box.profile("microsoft_ir"))
        save("SEC Tesla revenue concept",box.fetch("sec_concept",{"cik":"0001318605","taxonomy":"us-gaap","tag":"RevenueFromContractWithCustomerExcludingAssessedTax"}))
        save("California 2026 general-election portal",box.profile("california_elections"))
        save("California 2026 deadlines",box.profile("california_elections","https://www.sos.ca.gov/elections/upcoming-elections/general-election-november-3-2026/key-dates-deadlines"))
        save("California 2026 calendar PDF",box.profile("california_elections","https://elections.cdn.sos.ca.gov/statewide-elections/2026-primary/election-guide/section-07-general-election-calendar.pdf"))
        save("Brazil 2026 official election portal",box.profile("brazil_tse"))
        save("US election authority directory",box.profile("fec_election_directory"))
        listing=save("UK finance bill discovery",box.fetch("uk_bills",{"SearchTerm":"finance"}))
        if listing["records"]:
            bill_id=listing["records"][0]["billId"]
            save("UK exact bill detail",box.fetch("uk_bill_detail",{"bill_id":bill_id}))
            publications=save("UK exact bill publications",box.fetch("uk_bill_publications",{"bill_id":bill_id}))
            if publications["records"]:
                publication=publications["records"][0]
                documents=publication.get("documents",[])
                if documents:
                    document=documents[0]
                    pub_id=publication.get("id",publication.get("publicationId"))
                    doc_id=document.get("id",document.get("documentId"))
                    if pub_id and doc_id: save("UK bill publication original",box.read(f"https://bills-api.parliament.uk/api/v1/Publications/{pub_id}/Documents/{doc_id}/Download"))
        save("UK Data Use and Access Act as enacted",box.fetch("uk_legislation_xml",{"type":"ukpga","year":2025,"number":18,"version":"enacted"}))
        registry=save("Federal Register AI document discovery",box.fetch("federal_register",{"conditions[term]":"artificial intelligence"}))
        if registry["records"]:
            first=registry["records"][0]
            save("Federal Register exact document detail",box.fetch("federal_register_detail",{"document_number":first["document_number"]}))
        report["finished_at_utc"]=now()
        (root/"priority-report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    finally: box.close()


if __name__=="__main__": main()

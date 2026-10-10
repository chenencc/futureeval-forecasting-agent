"""Curated authority/issuer routes; source roles are not outcome assertions."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

PROFILES = {
    "tesla_ir": {"domain":"finance", "role":"issuer_disclosure", "entity":"Tesla, Inc.", "cik":"0001318605", "url":"https://ir.tesla.com/", "hosts":["ir.tesla.com","assets-ir.tesla.com","digitalassets.tesla.com","www.tesla.com"], "paths":["/"], "keywords":["earnings","shareholder","10-q","10-k","download","quarter","revenue","eps"], "caveat":"Fiscal period, GAAP/non-GAAP basis and units require exact source coordinates. A scheduled earnings date is not a released report."},
    "microsoft_ir": {"domain":"finance", "role":"issuer_disclosure", "entity":"Microsoft Corporation", "cik":"0000789019", "url":"https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q4/press-release-webcast", "hosts":["www.microsoft.com","cdn-dynmedia-1.microsoft.com"], "paths":["/en-us/investor/","/investor/reports/","/is/content/microsoftcorp/"], "keywords":["earnings","revenue","income","share","financial","quarter","download"], "caveat":"Microsoft fiscal years differ from calendar years; distinguish adjusted metrics and reported metrics."},
    "california_elections": {"domain":"politics", "role":"election_authority", "entity":"California Secretary of State", "jurisdiction":"US-CA", "url":"https://www.sos.ca.gov/elections/upcoming-elections/general-election-november-3-2026", "hosts":["www.sos.ca.gov","elections.cdn.sos.ca.gov","voterguide.sos.ca.gov","electionresults.sos.ca.gov"], "paths":["/elections/","/statewide-elections/","/"], "keywords":["2026","calendar","deadline","candidate","ballot","result","statement of vote","certif"], "caveat":"Election information and candidate certification do not establish certified vote results. Use state/county certification records for outcomes."},
    "brazil_tse": {"domain":"politics", "role":"election_authority", "entity":"Tribunal Superior Eleitoral", "jurisdiction":"BR", "url":"https://www.tse.jus.br/eleicoes/eleicoes-2026", "hosts":["www.tse.jus.br","static.tse.jus.br","resultados.tse.jus.br"], "paths":["/eleicoes/","/comunicacao/","/"], "keywords":["2026","calendário","resultado","turno","candidat","elei","resolu"], "caveat":"Preserve election office, round, geography and official tally status. An election portal is not a candidate victory or final certification."},
    "fec_election_directory": {"domain":"politics", "role":"authority_directory", "entity":"Federal Election Commission", "jurisdiction":"US", "url":"https://www.fec.gov/introduction-campaign-finance/election-results-and-voting-information/", "hosts":["www.fec.gov"], "paths":["/"], "keywords":["state","election","result","office","certif"], "caveat":"FEC covers campaign finance and historical compilations. Current result certification is a state/local authority responsibility."},
}


def resolve_profile(profile_id, url=None):
    profile=PROFILES[profile_id]
    target=url or profile["url"]
    parts=urlsplit(target)
    if parts.scheme!="https" or parts.hostname not in profile["hosts"] or parts.username or parts.password or parts.port not in (None,443):
        raise ValueError("URL does not belong to the declared issuer/authority profile")
    if not any(parts.path.lower().startswith(p) for p in profile["paths"]):
        raise ValueError("URL path is outside the declared source profile")
    return profile,target


class LinkInventory(HTMLParser):
    """Retain link labels and table-row context from issuer/authority pages."""
    def __init__(self,base):
        super().__init__(convert_charrefs=True)
        self.base=base
        self.links=[]
        self.active=None
        self.row=None
        self.row_links=[]
        self.skip=0

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in {"script","style"}: self.skip+=1
        if tag=="tr": self.row=[]; self.row_links=[]
        if tag=="a" and attrs.get("href"):
            self.active={"url":urljoin(self.base,attrs["href"]),"label":"","row_context":None}

    def handle_data(self,data):
        if self.skip: return
        if self.active is not None: self.active["label"]+=data
        if self.row is not None: self.row.append(data)

    def handle_endtag(self,tag):
        if tag in {"script","style"}: self.skip=max(0,self.skip-1)
        if tag=="a" and self.active is not None:
            link=self.active
            link["label"]=" ".join(link["label"].split())
            if urlsplit(link["url"]).scheme=="https":
                self.links.append(link)
                if self.row is not None: self.row_links.append(link)
            self.active=None
        if tag=="tr" and self.row is not None:
            context=" ".join(" ".join(self.row).split())[:1200]
            for link in self.row_links: link["row_context"]=context
            self.row=None
            self.row_links=[]


def inventory(raw,url,profile=None,limit=40):
    parser=LinkInventory(url)
    parser.feed(raw.decode("utf-8",errors="replace"))
    seen=set()
    rows=[]
    for link in parser.links:
        if link["url"] in seen: continue
        seen.add(link["url"])
        if profile:
            context=(link["label"]+" "+(link["row_context"] or "")+" "+link["url"]).casefold()
            if not any(word.casefold() in context for word in profile["keywords"]): continue
            link["within_profile_hosts"]=urlsplit(link["url"]).hostname in profile["hosts"]
        rows.append(link)
    return {"links":rows[:limit],"total_selected":len(rows),"truncated":len(rows)>limit,"full_archive_complete":False,"inherited_rowspan_context":"not reconstructed; consult original HTML for year cells spanning rows"}


class TableInventory(HTMLParser):
    """Preserve cell boundaries/spans and surrounding unit text; no value inference."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[]
        self.tables=[]
        self.prefix=""
        self.count=0
        self.skip=0

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in {"script","style"}: self.skip+=1
        if tag=="table":
            self.count+=1
            self.stack.append({"table_index":self.count,"context_before":self.prefix[-1000:],"rows":[],"row":None,"cell":None})
        if self.stack:
            table=self.stack[-1]
            if tag=="tr": table["row"]=[]
            if tag in {"td","th"}: table["cell"]={"text":"","tag":tag,"rowspan":attrs.get("rowspan","1"),"colspan":attrs.get("colspan","1")}

    def handle_data(self,data):
        if self.skip: return
        text=" ".join(data.split())
        if not text: return
        self.prefix=(self.prefix+" "+text)[-2000:]
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"]["text"]+=" "+text

    def handle_endtag(self,tag):
        if tag in {"script","style"}: self.skip=max(0,self.skip-1)
        if not self.stack: return
        table=self.stack[-1]
        if tag in {"td","th"} and table["cell"] is not None:
            table["cell"]["text"]=table["cell"]["text"].strip()
            if table["row"] is not None: table["row"].append(table["cell"])
            table["cell"]=None
        if tag=="tr" and table["row"] is not None:
            if any(c["text"] for c in table["row"]): table["rows"].append(table["row"])
            table["row"]=None
        if tag=="table":
            self.stack.pop()
            self.tables.append({k:v for k,v in table.items() if k not in {"row","cell"}})


def tables(raw,max_rows=200):
    parser=TableInventory()
    parser.feed(raw.decode("utf-8",errors="replace"))
    result=[]
    for table in sorted(parser.tables,key=lambda t:t["table_index"])[:30]:
        row_count=len(table["rows"])
        if not row_count: continue
        table["rows"]=table["rows"][:max_rows]
        table.update(total_rows=row_count,rows_truncated=row_count>max_rows)
        result.append(table)
    return {"tables":result,"total_tables":len(parser.tables),"tables_truncated":len(parser.tables)>30,"units_and_financial_basis_inferred":False,"merged_cells_normalized":False,"warning":"Preserve rowspan/colspan and surrounding headings. Calendar/fiscal quarter, GAAP basis and units require interpretation; no numeric conversion performed."}

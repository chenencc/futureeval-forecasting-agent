"""Reject a narrow demonstrable period/value contradiction, not unknown future outcomes."""
import re
SEASONS='DJF|JFM|FMA|MAM|AMJ|MJJ|JJA|JAS|ASO|SON|OND|NDJ'
ROWS=re.compile(r'(?m)^\s*('+SEASONS+r')\s+(\d{4})\s+([-+]?\d+(?:\.\d+)?)\s*$')
ASSERTION=re.compile(r'\b('+SEASONS+r')\s+(\d{4})\s+(?:entry|value|reading|observation)\s+(?:of|is|was|=|:)\s*([-+]?\d+(?:\.\d+)?)',re.I)

def unsupported_series_assertion(text,note):
    rows={(p,y):float(v) for p,y,v in ROWS.findall(text)}
    if len(rows)<5:return False
    for match in ASSERTION.finditer(note):
        # Explicit negation is a missing-observation statement, not a value claim.
        if re.search(r'\b(?:no|not|without|missing)\b',note[max(0,match.start()-35):match.start()],re.I):continue
        p,y,v=match.groups()
        if (p.upper(),y) not in rows or rows[p.upper(),y]!=float(v):return True
    return False

"""Calibrate school history dates against verified real request timestamps."""
from datetime import datetime,timezone
import re
from urllib.parse import urlsplit,parse_qs
from bs4 import BeautifulSoup


def dates(html):
    result={}
    for row in BeautifulSoup(html,'html.parser').select('tr'):
        cells=row.find_all('td',recursive=False)
        if len(cells)<5:continue
        match=re.search(r'(\d{4})[-./](\d{1,2})[-./](\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?',cells[0].get_text(' ',strip=True))
        raw=None
        if match:
            values=[int(v or 0) for v in match.groups()]
            raw=datetime(*values,tzinfo=timezone.utc).timestamp()
        for link in row.select('a[href]'):
            parts=urlsplit(link['href']);query=parse_qs(parts.query)
            eid=query.get('eid',[''])[0]
            if parts.path=='/local/ubmessage/email_send.php' and query.get('id')==['157301'] and eid.isdigit():result[eid]=raw
    return result


def calibrate(samples):
    """Require real receipt+subject+recipient matches, never assume host timezone."""
    scores={offset:sum(abs(raw+offset-actual)<=180 for raw,actual in samples if raw is not None) for offset in (0,-9*3600)}
    best=max(scores,key=scores.get)
    return best if scores[best]>0 and scores[best]>scores[-9*3600 if best==0 else 0] else None

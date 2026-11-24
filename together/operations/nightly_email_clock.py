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
    # The actual school HTML uses a 12-hour clock without an AM/PM suffix.
    # Detect this from real matched requests, rather than treating PM as UTC.
    twelve=sum(any(abs(t-actual)<=180 for t in possibilities(raw,'kst_12h')) for raw,actual in samples if raw is not None)
    best=max(scores,key=scores.get)
    if twelve>scores[best]:return 'kst_12h'
    return best if scores[best]>0 and scores[best]>scores[-9*3600 if best==0 else 0] else None


def possibilities(raw,clock):
    if raw is None or clock is None:return []
    if clock=='kst_12h':
        dt=datetime.fromtimestamp(raw,timezone.utc)
        if dt.hour==0 or dt.hour>12:return [raw-9*3600]
        midnight_half=dt.replace(hour=dt.hour%12).timestamp()-9*3600
        return [midnight_half,midnight_half+12*3600]
    return [raw+clock]


def membership(raw,clock,since,until,*,actual=None,lower=None,upper=None):
    candidates=possibilities(raw,clock)
    if not candidates:return None
    if actual is not None:
        close=[t for t in candidates if abs(t-actual)<=180]
        if not close:return None
        candidates=close
    else:
        bounded=[t for t in candidates if (lower is None or t>=lower-180) and (upper is None or t<=upper+180)]
        if bounded:candidates=bounded
    flags={since<=t<=until for t in candidates}
    return flags.pop() if len(flags)==1 else None

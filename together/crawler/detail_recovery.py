"""Reopen public source notices through the official listing, without guessing IDs."""
import base64
import hashlib
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from bs4 import BeautifulSoup
import requests

_source_limits={}


def remember_limit(url,response):
    if response is not None and response.status_code in (429,503):
        _source_limits[url]=time.time()+max(60,retry_delay(response.headers.get('Retry-After')))


def school(url):
    p=urlsplit(url)
    return p.scheme in ('http','https') and (p.hostname or '').endswith(('.pusan.ac.kr','.busan.ac.kr'))


def identity(url):
    if not school(url): return None
    p=urlsplit(url); q=parse_qs(p.query); path=p.path
    if q.get('enc'):
        try:
            val=q['enc'][0].replace(' ','+')
            path=unquote(base64.b64decode(val+'='*(-len(val)%4),validate=True).decode()).split('|@@|')[-1]
        except (ValueError,UnicodeError): return None
    k=re.search(r'/bbs/([\w-]+)/(\d+)/(\d+)/artclView\.do(?:$|[?])',path)
    if k:return ('k2web',k[1].lower(),k[2],k[3])
    if '/CMS/Board/' in path and all(q.get(k) for k in ('mgr_seq','board_seq')):
        return ('cms',p.hostname,q['mgr_seq'][0],q['board_seq'][0])
    if q.get('db') and q.get('seq'):
        return ('board',p.hostname,q['db'][0],q['seq'][0])
    return None


def listing(soup,url):
    rows={}
    for a in soup.select('a[href]'):
        link=urljoin(url,a['href']); key=identity(link)
        if not key:continue
        tr=a.find_parent(['tr','li'])
        text=' '.join(a.stripped_strings)
        text=re.sub(r'\s+(?:새글|new)$','',text,flags=re.I).strip()
        dt=tr.select_one('td._artclTdRdate,td._artclTdDate,td.td-date,td.date,time') if tr else None
        day=re.search(r'\d{4}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}',dt.get_text(strip=True)) if dt else None
        author=tr.select_one('td._artclTdWriter,td.td-write,td.writer') if tr else None
        label=author.get_text(strip=True) if author else ''
        num=tr.select_one('td.td-num,td._artclTdNum') if tr else None
        number=num.get_text(strip=True) if num else ''
        row={'link':link,'title':text,'day':day.group() if day else '',
             'number':number if number.isdecimal() else '',
             'writer_hash':hashlib.sha256(label.encode()).hexdigest() if label else ''}
        if text: rows[link]=row
    return rows


def replacement(original,expected,rows):
    """An ID change needs three matching fields on a unique row of the same board."""
    key=identity(original)
    if not key or not expected.get('title') or not expected.get('day'):return None
    found={u for u,r in rows.items() if identity(u) and identity(u)[:-1]==key[:-1]
           and all(r[k]==expected[k] for k in ('title','day'))}
    if len(found)!=1:return None
    row=rows[next(iter(found))]
    corroborator='writer_hash' if expected.get('writer_hash') else 'number'
    if not expected.get(corroborator) or row.get(corroborator)!=expected[corroborator]:return None
    return next(iter(found)) if len(found)==1 else None


def retry_delay(value):
    try:return max(0,int(value))
    except (ValueError,TypeError):
        try:return max(0,int((parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()))
        except (ValueError,TypeError,OverflowError):return 0


def probe(url,board,expected=None):
    expected=expected or {}
    result={'reason':'unreadable_source','retry_after':0}
    if not (school(url) and school(board)):return result
    wait=_source_limits.pop(url,0)-time.time()
    if wait>0:return {'reason':'school_rate_or_service_limit','retry_after':int(wait)+1}
    with requests.Session() as client:
        def fetch(u,referer,params=None):
            visited=set()
            while school(u) and u not in visited and len(visited)<10:
                visited.add(u)
                res=client.get(u,params=params,headers={'Referer':referer},timeout=(5,20),verify=False,allow_redirects=False)
                if res.status_code not in (301,302,303,307,308):break
                u=urljoin(res.url,res.headers.get('Location',''));params=None;res.close()
            else:raise ValueError('unverified redirect')
            if res.status_code in (429,503):
                result.update(reason='school_rate_or_service_limit',retry_after=retry_delay(res.headers.get('Retry-After')))
            res.raise_for_status()
            return res
        try:
            page=fetch(board,board);soup=BeautifulSoup(page.content,'html.parser')
            rows=listing(soup,page.url);key=identity(url)
            choices=[u for u in rows if key and identity(u)==key]
            searched=None
            if not choices and expected.get('title') and key and key[0]=='k2web':
                form=soup.select_one('form[name=searchForm][action]')
                action=urljoin(page.url,form['action']) if form else ''
                if (urlsplit(action).hostname==urlsplit(page.url).hostname
                    and urlsplit(action).path==f'/bbs/{key[1]}/{key[2]}/artclList.do'
                    and form.select_one('[name=findWord]') is not None):
                    params={'findType':'sj','findWord':expected['title'],'page':1}
                    matches={};seen_pages=set()
                    while True:
                        search=fetch(action,page.url,params)
                        found=BeautifulSoup(search.content,'html.parser')
                        form_check=found.select_one('form[name=searchForm] [name=findWord]')
                        if not form_check or form_check.get('value','')!=expected['title']:break
                        subset=listing(found,search.url);signature=frozenset(subset)
                        if signature in seen_pages:break
                        seen_pages.add(signature);matches.update(subset)
                        pages=[int(n) for a in found.select('a[href],a[onclick]') for n in re.findall(r'(?:page_link\(\s*|[?&]page=)(\d+)',str(a),re.I)]
                        if max(pages,default=1)<=params['page']:
                            searched=matches;rows.update(matches);break
                        params={**params,'page':params['page']+1}
                    choices=[u for u in rows if key and identity(u)==key]
            if not choices:
                moved=replacement(url,expected,searched or {})
                if moved:choices=[moved]
            result['listed']=bool(choices)
            for link in list(dict.fromkeys(choices+[url])):
                res=fetch(link,page.url);s=BeautifulSoup(res.content,'html.parser')
                alert=s.select_one('form._messageForm')
                if alert:
                    result['reason']='school_access_message' if '접근' in alert.get_text() or '권한' in alert.get_text() else 'school_message'
                    continue
                body=s.select_one('div.artclView,#boardContents,div.board-contents.clear,div.txt')
                if not body and s.select_one('input[type=password]'):
                    result['reason']='school_login_required';continue
                if not body:
                    result['reason']='detail_layout_unverified';continue
                if identity(link) and identity(res.url) and identity(link)!=identity(res.url):continue
                if link!=url and not (identity(link) and identity(link)==identity(res.url)):continue
                result.update(response=res,link=link,expected_title=(rows.get(link) or {}).get('title',''))
                return result
        except requests.RequestException as exc:
            if result['reason']=='unreadable_source':result['reason']=type(exc).__name__
        except (ValueError,UnicodeError):result['reason']='unverified_listing_or_redirect'
    return result

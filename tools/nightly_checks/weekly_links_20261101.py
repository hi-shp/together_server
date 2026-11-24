import os
import collections,concurrent.futures,contextlib,csv,io,json,os,sys,tempfile,threading,time,types
from pathlib import Path
from datetime import datetime
from urllib.parse import urlsplit,urljoin
import requests,urllib3
from bs4 import BeautifulSoup
from dotenv import load_dotenv
R=Path(os.environ.get('TOGETHER_ROOT','/home/ubuntu/together/Source'));O=Path(os.environ['TOGETHER_AUDIT_OUTPUT']);sys.path.insert(0,str(R));load_dotenv(R/'.env');urllib3.disable_warnings()
sys.modules['together.integrations.openai_client']=types.SimpleNamespace(check_title_similarity=None,answer_gpt=None)
from together.crawler import announcements as ann
from together.crawler.pages import PageUrlManager
from together.crawler.onestop import menu_for,OnestopClient
os.chdir(R);pages=PageUrlManager().announcement_pages;network_get=ann._get_board_response
original_yesterday=(R/'yesterday.txt').read_bytes(); lock=threading.Lock();local=threading.local();last={}
def fetch(page):
    if menu_for(page.page_url):return page,None,None
    from together.operations.nightly_http import cached_response
    response=cached_response(Path(os.environ['PNU_DISCOVERY_DIR'])/'html',page.page_url)
    if response is not None:return page,response,None
    host=urlsplit(page.page_url).hostname
    with lock:delay=max(0,last.get(host,0)+.6-time.monotonic());last[host]=time.monotonic()+delay
    time.sleep(delay)
    if not hasattr(local,'session'):local.session=requests.Session()
    try:
        response=local.session.get(page.page_url,timeout=(5,25),verify=False);response.raise_for_status();return page,response,None
    except requests.RequestException as exc:return page,None,type(exc).__name__
def bounded_fetch(pool, pages):
    for start in range(0,len(pages),6):
        yield from pool.map(fetch,pages[start:start+6])

total_pages=len(pages)
checkpoint=O/'links-checkpoint.json'
results=json.loads(checkpoint.read_text()) if checkpoint.exists() else []
completed={v['source_url'] for v in results}
pages=[p for p in pages if p.source_url not in completed]
client=None
with tempfile.TemporaryDirectory(prefix='weekly-readonly-parser-') as temp:
    os.chdir(temp)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for page,response,error in bounded_fetch(pool, pages):
            row={'source_url':page.source_url,'checked_url':page.page_url,'department':page.department,'saved_number':page.number,'result':'request_error' if error else 'pending'}
            if response is not None:row['snapshot_response_reused']=getattr(response,'audit_snapshot_reused',False)
            if error:row['error']=error
            elif menu_for(page.page_url):
                try:
                    client=client or OnestopClient();code=menu_for(page.page_url)
                    selected,maximum,count=client.recent(code,page.number)
                    sample,info=client.page(code,0);second,info2=client.page(code,1)
                    row.update(result='verified_authenticated',new_candidates=len(selected),latest_number=maximum,display_count=count,first_page_rows=len(sample),second_page_rows=len(second),public_attachment_metadata=sum(len(v['files']) for v in sample))
                    # Read an existing body only; no files, draft, post or send.
                    if sample:
                        url=f"https://onestop.pusan.ac.kr/page?menuCD={code}&mode=DETAIL&seq={sample[0]['POSTING_SEQ_NO']}"
                        article=client.article(url,category=None);row['body_verified']=bool(article.content_text or '<img' in article.content_html)
                except Exception as exc:row.update(result='authenticated_review',error=type(exc).__name__)
                finally:
                    if client:client.close();client=None
            else:
                Path('yesterday.txt').write_bytes(original_yesterday);outcomes=[];diagnostics=[];deferred=[]
                ann._get_board_response=lambda url, **kw: response if url==page.page_url else network_get(url,**kw)
                ann.record_board_result=lambda value:outcomes.append(value)
                ann.record_board_deferred=lambda *args:deferred.append(True)
                ann.event=lambda *a,**k:diagnostics.append({'message':a[1] if len(a)>1 else '',**{q:k[q] for q in ('level','outcome') if q in k}})
                ann.recent_title=lambda:[]
                try:
                    with contextlib.redirect_stdout(io.StringIO()):urls,maximum=ann.get_anns_url(page,len(results)+1)
                    soup=BeautifulSoup(response.text,'html.parser');numbers=[int(v.get_text(strip=True)) for v in soup.select('td._artclTdNum,td.number,td.num,td.td-num') if v.get_text(strip=True).isdigit()]
                    examples=[urljoin(response.url,a['href']) for a in soup.select('td._artclTdTitle a[href],td.subject a[href],td.td-title a[href]')]
                    row.update(result='response_deferred' if deferred else 'verified' if outcomes==[True] else 'school_disabled' if outcomes==[None] else 'parser_review',parser_outcomes=outcomes,new_candidates=len(urls),next_number=maximum,status=response.status_code,final_url=response.url,has_tbody=soup.find('tbody') is not None,numeric_rows=len(numbers),latest_numeric=max(numbers,default=None),sample_detail=next(iter(examples),None),diagnostics=diagnostics)
                except Exception as exc:row.update(result='parser_review',error=type(exc).__name__)
            results.append(row)
            (O/'links-checkpoint.json').write_text(json.dumps(results,ensure_ascii=False))
            if len(results)%100==0:
                (O/'links-progress.json').write_text(json.dumps({'checked':len(results),'expected':total_pages,'states':dict(collections.Counter(v['result'] for v in results))}));print(len(results),flush=True)
    if client:client.close()
    os.chdir(R)
proof={'checked_at':datetime.now().astimezone().isoformat(),'physical_boards':total_pages,'checked':len(results),'states':dict(collections.Counter(v['result'] for v in results)),'csv_yesterday_not_written':(R/'yesterday.txt').read_bytes()==original_yesterday,'results':results,'post_send_download_requests':0}
p=O/'links.json';p.write_text(json.dumps(proof,ensure_ascii=False,indent=2));p.chmod(0o600)
print(json.dumps({k:v for k,v in proof.items() if k!='results'},ensure_ascii=False))

import os
"""Read every page of the public university homepage registry."""
import json,time,re,urllib3
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
urllib3.disable_warnings()
root=Path(os.getenv('PNU_DISCOVERY_DIR') or Path(__file__).resolve().parent/'pnu-discovery');root.mkdir(parents=True,exist_ok=True)
out=[];proof=[]
session=requests.Session()
response=session.get('https://his.pusan.ac.kr/sites/his/index.do',timeout=(5,20),verify=False)
response.raise_for_status()
soup=BeautifulSoup(response.content,'html.parser')
for group in ('dept','prof'):
 seen={};page=1;expected=None;pages=[]
 while True:
  forms=[f for f in soup.select('form') if f.get('action')=='/hisIndex/his/index.do']
  assert forms,'Public registry form missing'
  form=forms[-1]
  payload={x['name']:x.get('value','') for x in form.select('input[type=hidden][name]')}
  payload.update(findGbn=group,findCate='',findWord='',findType='',page=str(page))
  with session.post('https://his.pusan.ac.kr/hisIndex/his/index.do',data=payload,timeout=(5,20),verify=False) as response:
   response.raise_for_status();soup=BeautifulSoup(response.content,'html.parser')
   rows=[]
   for tr in soup.select('tbody tr'):
    cells=tr.find_all('td')
    if len(cells)<4 or not cells[0].get_text(strip=True).isdigit():continue
    number=int(cells[0].get_text(strip=True))
    a=cells[-1].find('a',href=True)
    url=urljoin(response.url,a['href']) if a and a['href'].startswith(('http://','https://','/')) else None
    rows.append({'registry_kind':group,'registry_number':number,'registered_at':cells[1].get_text(' ',strip=True),'category':cells[2].get_text(' ',strip=True),'name':cells[-1].get_text(' ',strip=True),'url':url,'registry_page':page,'registry_source':'https://his.pusan.ac.kr/hisIndex/his/index.do'})
   if page==1:
    assert rows,'Registry did not return first-page rows'
    expected=max(r['registry_number'] for r in rows)
   new=[r for r in rows if r['registry_number'] not in seen]
   assert new or len(seen)==expected,'Unexpected repeated or empty registry page'
   for r in new:seen[r['registry_number']]=r
   pages.append({'page':page,'returned':len(rows),'new':len(new)})
   print(json.dumps({'group':group,'page':page,'covered':len(seen),'expected':expected}),flush=True)
  if len(seen)>=expected:break
  page+=1;time.sleep(0.4)
 assert len(seen)==expected and set(seen)==set(range(1,expected+1)),'Registry coverage gap'
 out.extend(seen.values());proof.append({'group':group,'expected':expected,'covered':len(seen),'pages':pages,'complete':True})
(root/'official-registry.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
(root/'official-registry-coverage.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
(root/'extra-seeds.json').write_text(json.dumps([r for r in out if r['url']],ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'records':len(out),'with_url':sum(bool(r['url']) for r in out),'groups':[{k:v for k,v in p.items() if k!='pages'} for p in proof]}),flush=True)


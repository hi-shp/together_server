"""PLATO 전송 양식을 읽어 검증된 수신자에게 한 번씩 발송한다."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import signal
from contextlib import contextmanager
from urllib.parse import urljoin,urlsplit,parse_qs

from bs4 import BeautifulSoup
import requests

HOST='dev-plato.pusan.ac.kr'
COURSE='157301'
BASE=f'https://{HOST}/local/ubmessage/'


class NotificationError(RuntimeError):pass
class NotificationOutcomeUnknown(NotificationError):pass


def normalize(value):return ' '.join(str(value).split())


@contextmanager
def request_deadline(seconds):
    def expired(signum,frame):raise TimeoutError('Notification deadline exceeded')
    previous=signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,seconds)
    try:yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)


def parse_send_form(html,kind,page_url):
    soup=BeautifulSoup(html,'html.parser')
    forms=[form for form in soup.select('form') if urljoin(page_url,form.get('action',''))==BASE+'action.php' and form.get('method','').lower()=='post']
    if len(forms)!=1:raise NotificationError('Notification form unavailable')
    form=forms[0]
    hidden={node['name']:node.get('value','') for node in form.select('input[type="hidden"][name]')}
    expected='message_send_group' if kind=='message' else 'email_send'
    if hidden.get('type')!=expected or hidden.get('courseid')!=COURSE:
        raise NotificationError('Notification form scope mismatch')
    targets=[]
    labels={node.get('for'):node for node in soup.select('label[for]')}
    for node in form.select('input[type="checkbox"][name="tousers[]"]'):
        value=node.get('value','')
        if not value.isdigit():raise NotificationError('Invalid recipient ID')
        label=labels.get(node.get('id'))
        if label:
            targets.append({'id':value,'text':normalize(label.get_text(' ',strip=True)),
                            'title':normalize(label.get('title',''))})
    return hidden,targets


def resolve_recipients(targets,recipients,kind):
    ids=[];missing=[]
    for person in recipients:
        name=normalize(person['name'])
        identity=person['student_id'] if kind=='message' else person['email']
        wanted=normalize(f'{name} ({identity})')
        matches=[target['id'] for target in targets if target['title']==wanted or target['text']==wanted
                 or (kind=='message' and target['text'].startswith(wanted+' '))]
        if len(set(matches))==1:ids.append(matches[0])
        else:missing.append(hashlib.sha256(wanted.encode()).hexdigest()[:12])
    return list(dict.fromkeys(ids)),missing


class DeliveryLedger:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.connection=sqlite3.connect(self.path,timeout=10)
        os.chmod(self.path,0o600)
        self.connection.execute('CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY,kind TEXT,state TEXT,updated REAL,recipients INTEGER,seconds REAL,detail TEXT)')
        self.connection.execute('DELETE FROM deliveries WHERE updated<? AND state IN (\'sent\',\'rejected\')',(time.time()-90*86400,))
        self.connection.commit()

    def claim(self,key,kind,count):
        self.connection.execute('BEGIN IMMEDIATE')
        row=self.connection.execute('SELECT state FROM deliveries WHERE id=?',(key,)).fetchone()
        if row:
            self.connection.commit();return row[0]
        self.connection.execute('INSERT INTO deliveries VALUES (?,?,?,?,?,?,?)',(key,kind,'sending',time.time(),count,0,''))
        self.connection.commit();return 'new'

    def state(self,key):
        row=self.connection.execute('SELECT state FROM deliveries WHERE id=?',(key,)).fetchone()
        return row[0] if row else None

    def finish(self,key,state,seconds,detail=''):
        self.connection.execute('UPDATE deliveries SET state=?,updated=?,seconds=?,detail=? WHERE id=?',
                                (state,time.time(),seconds,detail,key));self.connection.commit()

    def close(self):self.connection.close()


def response_is_success(response):
    # 실제 서버의 완료 응답 문구를 기준으로 전송 접수를 확인한다.
    if response.status_code not in (200,302,303):return False
    text=response.text
    return bool(re.search(r'(?:메세지|메시지|이메일|메일)(?:가|이)?\s*(?:전달|전송|발송)되었습니다|전송(?:이|을)?\s*(?:완료|되었습니다|하였습니다)|성공적으로\s*전송|발송(?:이)?\s*(?:완료|되었습니다)|successfully\s+sent',text,re.I))


def response_recipient_count(text):
    match=re.search(r'(\d+)명에게\s*(?:메세지|메시지|이메일|메일)(?:가|이)?\s*(?:전달|전송|발송)되었습니다',text)
    return int(match[1]) if match else None


def parse_email_history(html):
    soup=BeautifulSoup(html,'html.parser');records=[]
    for row in soup.select('tr'):
        cells=row.find_all('td',recursive=False)
        if len(cells)<5:continue
        links=[a for a in row.select('a[href]') if urlsplit(a['href']).path=='/local/ubmessage/email_send.php']
        for link in links:
            query=parse_qs(urlsplit(link['href']).query)
            if query.get('id')==[COURSE] and query.get('eid',[''])[0].isdigit():
                student_ids=set()
                for node in cells[2].select('[title]'):
                    match=re.search(r'\((\d{7,12})\)',node['title'])
                    if match:student_ids.add(match[1])
                records.append({'eid':query['eid'][0],'subject':normalize(cells[1].get_text()),'student_ids':student_ids})
    return records


class PlatoNotificationTransport:
    def __init__(self,driver,base_dir):
        self.session=requests.Session()
        for cookie in driver.get_cookies():
            self.session.cookies.set(cookie['name'],cookie['value'],domain=HOST,path=cookie.get('path','/'))
        self.session.headers['User-Agent']=driver.execute_script('return navigator.userAgent')
        self.base_dir=Path(base_dir)
        self.ledger=DeliveryLedger(self.base_dir/'.runtime/notification-deliveries.sqlite3')
        self.student_ids={}

    def close(self):self.session.close();self.ledger.close()

    def prepare(self,kind,recipients):
        if kind not in ('message','email'):raise ValueError('Unknown notification kind')
        page=BASE+('send.php' if kind=='message' else 'email_send.php')+'?id='+COURSE+'&lang=ko'
        with request_deadline(45),self.session.get(page,timeout=(5,30)) as response:
            if response.status_code!=200 or urlsplit(response.url).netloc!=HOST or '/login/' in response.url:
                raise NotificationError('Notification login failed')
            hidden,targets=parse_send_form(response.text,kind,page)
        ids,missing=resolve_recipients(targets,recipients,kind)
        if kind=='message':
            for target in targets:
                match=re.search(r'\((\d{7,12})\)',target['title'] or target['text'])
                if match:self.student_ids[target['id']]=match[1]
        return hidden,ids,missing

    def email_history(self):
        with request_deadline(45),self.session.get(BASE+'email_log.php?id='+COURSE,timeout=(5,30),allow_redirects=False) as response:
            if response.status_code!=200:raise NotificationError('Email receipt history unavailable')
            return parse_email_history(response.text)

    def send(self,kind,recipients,subject,body,event_key,*,test_only_id=None):
        started=time.monotonic()
        hidden,ids,missing=self.prepare(kind,recipients)
        if test_only_id and ids!=[str(test_only_id)]:raise NotificationError('Self-test recipient mismatch')
        path=self.base_dir/'.runtime'/f'notification-missing-{kind}.json'
        pending=path.with_suffix('.pending')
        pending.write_text(json.dumps({'updated':time.time(),'kind':kind,'requested':len(recipients),'missing_hashes':missing}),encoding='utf-8')
        os.chmod(pending,0o600);os.replace(pending,path)
        if len(missing)>=10 and len(missing)/max(1,len(recipients))>=.25:
            raise NotificationError('Recipient directory mismatch')
        sent=0;deduplicated=0
        for start in range(0,len(ids),100):
            batch=ids[start:start+100]
            key=hashlib.sha256(json.dumps([kind,event_key,batch],ensure_ascii=False).encode()).hexdigest()
            state=self.ledger.state(key)
            if state=='sent':deduplicated+=len(batch);continue
            if state is not None:raise NotificationOutcomeUnknown('Prior delivery needs verification')
            previous_email_ids=set();expected_student_ids=set()
            if kind=='email':
                if any(value not in self.student_ids for value in batch):self.prepare('message',[])
                if any(value not in self.student_ids for value in batch):raise NotificationError('Email recipient identity unavailable')
                expected_student_ids={self.student_ids[value] for value in batch}
                previous_email_ids={record['eid'] for record in self.email_history()}
            state=self.ledger.claim(key,kind,len(batch))
            if state=='sent':deduplicated+=len(batch);continue
            if state!='new':raise NotificationOutcomeUnknown('Concurrent delivery needs verification')
            payload=list(hidden.items())+[('tousers[]',value) for value in batch]
            if kind=='message':payload.append(('message',body))
            else:payload.extend([('subject',subject),('message[text]',body)])
            attempt=time.monotonic()
            try:
                with request_deadline(90):
                    response=self.session.post(BASE+'action.php',data=payload,timeout=(5,80),allow_redirects=False,
                                               headers={'Referer':BASE+('send.php' if kind=='message' else 'email_send.php')+'?id='+COURSE+'&lang=ko'})
            except (requests.RequestException,TimeoutError) as exc:
                self.ledger.finish(key,'unknown',time.monotonic()-attempt,type(exc).__name__)
                raise NotificationOutcomeUnknown('Delivery response unavailable') from None
            with response:
                count=response_recipient_count(response.text)
                confirmed=response_is_success(response) and (count is None or count==len(batch))
                detail='unrecognized_response'
                if kind=='email' and response.status_code in (200,302,303):
                    try:
                        records=self.email_history()
                        confirmed=any(record['eid'] not in previous_email_ids and record['subject']==normalize(subject)
                                      and record['student_ids']==expected_student_ids for record in records)
                    except (NotificationError,requests.RequestException,TimeoutError):
                        confirmed=False;detail='email_history_unavailable'
                if confirmed:
                    self.ledger.finish(key,'sent',time.monotonic()-attempt);sent+=len(batch)
                else:
                    private=self.base_dir/'.runtime'/('notification-response-'+key[:16]+'.html')
                    private.write_text(response.text[:200000],encoding='utf-8');os.chmod(private,0o600)
                    private.with_suffix('.json').write_text(json.dumps({'status':response.status_code,'count':count,
                        'previous_email_ids':sorted(previous_email_ids),'expected_student_ids':sorted(expected_student_ids)}),encoding='utf-8')
                    os.chmod(private.with_suffix('.json'),0o600)
                    self.ledger.finish(key,'unknown',time.monotonic()-attempt,detail)
                    raise NotificationOutcomeUnknown('Delivery confirmation unavailable')
        return {'requested':len(recipients),'matched':len(ids),'missing':len(missing),'sent':sent,
                'deduplicated':deduplicated,'seconds':time.monotonic()-started,'transport':'authenticated_form_post'}

"""One Discord category alert per scholarship title/occurrence, across boards and runs."""
from pathlib import Path
from datetime import datetime
import hashlib,json,os,re,sqlite3,time
from together.notices.intelligence.text import normalize_title,identity

PREFIX=re.compile(r'^\[(?:장학금?|장학공지|공지|안내|필독|중요|학부|대학원|학부\s*[/·]\s*대학원|전체)\]\s*')

def alert_title(content):
    match=re.fullmatch(r'(.+?)\s*\n[＞>]\s*장학금\s*',str(content or ''),re.S)
    return match.group(1).strip() if match else None

def alert_key(title,at=None):
    value=normalize_title(title)
    value=re.sub(r'^[⭐★☆\s]+','',value)
    while PREFIX.match(value):value=PREFIX.sub('',value)
    value=re.sub(r'((?:19|20)\d{2})(?:학년도|년도)',r'\1년',value)
    value=re.sub(r'\s*(?:안내|알림)\s*$','',value)
    occurrence=identity(value)
    if not occurrence['years']:occurrence['years']=[str(datetime.fromtimestamp(time.time() if at is None else at).year)]
    # Keep numeric boundaries so 11/1 and 1/11 cannot collapse into the same key.
    numbers=[re.sub(r'[.:-]','/',n) for n in re.findall(r'\d+(?:[./:-]\d+)*',value)]
    text=''.join(c.lower() for c in value if c.isalnum())
    return hashlib.sha256(json.dumps([text,occurrence,numbers],sort_keys=True,ensure_ascii=False).encode()).hexdigest()

class ScholarshipAlerts:
    def __init__(self,base_dir):
        self.path=Path(base_dir)/'.runtime/scholarship-discord.sqlite3'
        self.path.parent.mkdir(parents=True,exist_ok=True)
    def connect(self):
        db=sqlite3.connect(self.path,timeout=5)
        db.execute('CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY,at REAL,state TEXT,message_id TEXT)')
        os.chmod(self.path,0o600)
        return db
    def claim(self,title):
        key=alert_key(title)
        with self.connect() as db:
            result=db.execute('INSERT OR IGNORE INTO alerts VALUES (?,?,?,?)',(key,time.time(),'attempted',''))
        db.close()
        return key if result.rowcount else None
    def complete(self,key,sent,message_id=None):
        with self.connect() as db:db.execute('UPDATE alerts SET state=?,message_id=? WHERE id=?',('sent' if sent else 'unknown',message_id or '',key))
        db.close()
    def remember_sent(self,title,at,message_id):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO alerts VALUES (?,?,?,?)',(alert_key(title,at),at,'sent',message_id))
        db.close()

"""첨부파일을 내용별로 묶어 7일간 보관한다."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

GIB=1024**3


def file_digest(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


class AttachmentCache:
    def __init__(self,base_dir,max_bytes=None,min_free=None,retention_days=7):
        self.root=Path(base_dir)/'.runtime/attachments'
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.root.is_symlink():raise ValueError('Invalid attachment cache path')
        os.chmod(self.root,0o700)
        self.objects=self.root/'objects';self.objects.mkdir(mode=0o700,exist_ok=True)
        if self.objects.is_symlink():raise ValueError('Invalid attachment object path')
        self.max_bytes=int(max_bytes if max_bytes is not None else float(os.getenv('ATTACHMENT_CACHE_GB','10'))*GIB)
        self.min_free=int(min_free if min_free is not None else float(os.getenv('ATTACHMENT_MIN_FREE_GB','5'))*GIB)
        self.retention=retention_days*86400

    @contextmanager
    def transaction(self):
        with (self.root/'cache.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            conn=sqlite3.connect(self.root/'index.sqlite3',timeout=5)
            os.chmod(self.root/'index.sqlite3',0o600)
            conn.execute('CREATE TABLE IF NOT EXISTS objects (sha TEXT PRIMARY KEY,bytes INTEGER,last_seen REAL)')
            conn.execute('CREATE TABLE IF NOT EXISTS names (sha TEXT,name TEXT,source TEXT,PRIMARY KEY(sha,name,source))')
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _remove(self,conn,sha):
        if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('Invalid cache digest')
        path=self.objects/sha
        if path.is_symlink():raise ValueError('Unexpected cache symlink')
        path.unlink(missing_ok=True)
        conn.execute('DELETE FROM names WHERE sha=?',(sha,));conn.execute('DELETE FROM objects WHERE sha=?',(sha,))

    def _prune(self,conn,reserve=0):
        cutoff=time.time()-self.retention
        rows=list(conn.execute('SELECT sha,bytes,last_seen FROM objects ORDER BY last_seen'))
        total=sum(row[1] for row in rows)
        for sha,size,seen in rows:
            if seen<cutoff or total+reserve>self.max_bytes:
                self._remove(conn,sha);total-=size
        return total

    def add(self,path,source_url=''):
        path=Path(path)
        if not path.is_file() or path.is_symlink():raise ValueError('Invalid attachment path')
        size=path.stat().st_size;sha=file_digest(path)
        with self.transaction() as conn:
            self._prune(conn)
            if conn.execute('SELECT 1 FROM objects WHERE sha=?',(sha,)).fetchone():
                conn.execute('UPDATE objects SET last_seen=? WHERE sha=?',(time.time(),sha))
            else:
                if size>self.max_bytes or shutil.disk_usage(self.root).free-size<self.min_free:
                    return {'stored':False,'reason':'capacity_or_free_space','sha256':sha,'bytes':size}
                self._prune(conn,size)
                with tempfile.NamedTemporaryFile(dir=self.objects,prefix='.pending-',delete=False) as handle:
                    pending=Path(handle.name)
                try:
                    shutil.copyfile(path,pending);os.chmod(pending,0o600)
                    if file_digest(pending)!=sha:raise ValueError('Attachment cache checksum mismatch')
                    os.replace(pending,self.objects/sha)
                finally:pending.unlink(missing_ok=True)
                conn.execute('INSERT INTO objects VALUES (?,?,?)',(sha,size,time.time()))
            conn.execute('INSERT OR IGNORE INTO names VALUES (?,?,?)',(sha,path.name,source_url))
        return {'stored':True,'sha256':sha,'bytes':size}

    def maintain(self):
        with self.transaction() as conn:
            total=self._prune(conn)
            count=conn.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
        return {'bytes':total,'objects':count,'limit_bytes':self.max_bytes,'retention_days':self.retention/86400,
                'free_bytes':shutil.disk_usage(self.root).free}


def archive_files(announcement,base_dir):
    if not announcement.files:return
    try:
        cache=AttachmentCache(base_dir)
        for path in announcement.files:
            result=cache.add(path,announcement.url)
            if not result['stored']:print('첨부 보관 여유 공간 부족: 원본 게시 처리를 이어갑니다.',flush=True)
    except Exception as exc:
        print(f'첨부 보관 생략: {type(exc).__name__}',flush=True)

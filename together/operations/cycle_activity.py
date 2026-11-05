"""Summarize this traversal only; never count a queued notification as sent."""
import os
import sqlite3
from collections import Counter
from pathlib import Path


def tick(kind):
    journal = os.getenv('TOGETHER_ACTIVITY_PATH')
    if journal:
        try:
            with open(journal, 'a', encoding='ascii') as stream:
                stream.write(kind+'\n')
        except OSError:
            # An unreadable activity journal must not interrupt a publication.
            print('WARN  활동 집계 기록 실패', flush=True)


def receipts(root):
    db = Path(root)/'.runtime/notification-deliveries.sqlite3'
    if not db.exists(): return set()
    try:
        with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True, timeout=3) as con:
            return set(con.execute("SELECT id,kind FROM deliveries WHERE state='sent' AND COALESCE(detail,'')!='final_retry_assumed_unverified'"))
    except sqlite3.Error:
        return None


def summarize(journal, before, after, exit_code):
    counts=Counter(journal.read_text(encoding='ascii').splitlines()) if journal.exists() else Counter()
    delivered=Counter(kind for _,kind in after-before) if before is not None and after is not None else None
    if exit_code and not counts['error']: counts['error']=1
    if not (counts or delivered or delivered is None): return ''
    emails=delivered['email'] if delivered is not None else '미확인'
    notes=delivered['message'] if delivered is not None else '미확인'
    return (f"⭐ 새 공지 {counts['new']}건 | 게시글 작성 {counts['post']}건 | 이메일 발송 {emails}건 | "
            f"쪽지 발송 {notes}건 | 보강 {counts['enriched']}건 | 오류 {counts['error']}회 | 경고 {counts['warn']}회")

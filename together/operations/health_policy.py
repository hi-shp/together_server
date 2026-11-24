"""Read-only monitoring of the existing email and API-to-Work policies."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time


def email_confirmation_grace(base, db, now):
    """Only an actual original email attempt starts the twelve-hour wait."""
    columns = {r[1] for r in db.execute('PRAGMA table_info(deliveries)')}
    if not {'id', 'kind', 'state'}.issubset(columns):
        return set(), set()
    waiting = set()
    for key, in db.execute("SELECT id FROM deliveries WHERE kind='email' AND state='unknown'"):
        try:
            attempt = json.loads((Path(base)/'.runtime/notification-attempts'/(key+'.json')).read_text())
            age = now - float(attempt['started_at'])
            if 0 <= age < 12 * 3600:
                waiting.add(key)
        except (OSError, ValueError, TypeError, KeyError):
            pass
    events = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='delivery_recipients'").fetchone():
        for event, key in db.execute("SELECT event,delivery_id FROM delivery_recipients WHERE kind='email' AND state!='sent'"):
            events.setdefault(event, set()).add(key)
    return waiting, {event for event, keys in events.items() if keys and keys <= waiting}


def waiting_email_job(payload, waiting_events):
    try:
        event = json.loads(payload)['event_key']
        return hashlib.sha256(json.dumps(['email', event], ensure_ascii=False).encode()).hexdigest() in waiting_events
    except (ValueError, TypeError, KeyError):
        return False


def work_credit_fallback_ready(base, now=None):
    """No model invocation: require configured files and a recent real success."""
    base = Path(base)
    now = time.time() if now is None else now
    try:
        config = json.loads((base/'.runtime/decision-provider.json').read_text())
        if config.get('policy') != 'api_credit_then_work':
            return False
        root = base/'.runtime/work-codex'
        if not (root/'auth.json').is_file() or not Path(config.get('work_binary') or '/nonexistent').is_file():
            return False
        latest_success = latest_failure = 0
        for path in (root/'decisions.sqlite3', root/'nightly/decisions.sqlite3'):
            if not path.is_file():
                continue
            with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as db:
                for state, at in db.execute('SELECT state,MAX(at) FROM attempts GROUP BY state'):
                    if state == 'complete':
                        latest_success = max(latest_success, at or 0)
                    elif state == 'unconfirmed':
                        latest_failure = max(latest_failure, at or 0)
        return latest_success > latest_failure and 0 <= now-latest_success < 30 * 86400
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return False

"""Persistent API-credit exhaustion, separate from daily processing limits."""
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


class CreditUnavailable(RuntimeError):
    pass


def credit_exhausted(error):
    """Rate limiting and network failures do not establish an empty balance."""
    body = getattr(error, 'body', None)
    if not isinstance(body, dict):
        return False
    details = body.get('error', body)
    if not isinstance(details, dict):
        return False
    return details.get('code') in ('insufficient_quota', 'credit_balance_exhausted')


class DecisionProvider:
    def __init__(self, base_dir):
        self.base = Path(base_dir)
        path = self.base / '.runtime/decision-provider.json'
        self.config = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        self.enabled = self.config.get('policy') == 'api_credit_then_work'

    @contextmanager
    def db(self):
        path = self.base / '.runtime/decision-provider.sqlite3'
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(path, timeout=10)
        path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY,at REAL,maximum REAL,state TEXT)')
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @property
    def exhausted(self):
        if not self.enabled:
            return False
        with self.db() as db:
            return db.execute("SELECT 1 FROM state WHERE key='api_exhausted'").fetchone() is not None

    def mark_exhausted(self, reason):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO state VALUES (?,?)',
                       ('api_exhausted', json.dumps({'at': time.time(), 'reason': reason})))

    def reserve(self, model, request):
        from .api_usage import ApiAccounting, RATES
        if self.exhausted:
            raise CreditUnavailable('API credit exhausted; use Work')
        rates = RATES.get(model)
        grants = self.config.get('credit_grants', [])
        if not rates or not grants:
            raise CreditUnavailable('Credit price or authorization unavailable')
        # The adapter disables automatic retries: unknown paid outcomes are not replayed.
        maximum = ((len(json.dumps(request, ensure_ascii=False).encode()) + 2000) * rates[0] + 500 * rates[2]) / 1e6
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            with ApiAccounting(self.base).connection() as usage:
                spent, unknown = usage.execute('SELECT COALESCE(SUM(cost),0),SUM(cost IS NULL) FROM usage WHERE at>=?',
                                              (self.config['balance_observed_at'],)).fetchone()
            pending = db.execute("SELECT COALESCE(SUM(maximum),0) FROM reservations WHERE state!='settled'").fetchone()[0]
            if unknown:
                raise CreditUnavailable('Unknown API costs must be reconciled before spending')
            remaining_spent = spent + pending + self.config.get('prior_unsettled_usd', 0)
            available = 0
            for grant in sorted(grants, key=lambda row: row['expires_at']):
                used = min(remaining_spent, grant['balance_usd'])
                remaining_spent -= used
                if datetime.fromisoformat(grant['expires_at']).timestamp() > time.time():
                    available += grant['balance_usd'] - used
            if available < maximum:
                # Roll back this short transaction before writing the permanent switch.
                reason = 'authorized_credit_remaining_below_one_request'
            else:
                key = uuid.uuid4().hex
                db.execute('INSERT INTO reservations VALUES (?,?,?,?)', (key, time.time(), maximum, 'pending'))
                return key
        self.mark_exhausted(reason)
        raise CreditUnavailable(reason)

    def settle(self, key, response=None, error=None):
        # A received SDK error without usage cannot be charged as a completion.
        # Transport/unknown outcomes retain their reservation for reconciliation.
        known_error = getattr(error, 'status_code', None) in (400, 401, 403, 404, 429)
        if key and (getattr(response, 'usage', None) is not None or known_error):
            with self.db() as db:
                db.execute("UPDATE reservations SET state='settled' WHERE id=?", (key,))

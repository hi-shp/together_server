"""Read durable audit receipts without duplicating records still in progress logs."""
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def retained_audit_events(base_dir, since, until):
    path = Path(base_dir) / '.runtime/audit-events.sqlite3'
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        return [json.loads(row[0]) for row in db.execute(
            'SELECT payload FROM events WHERE at>=? AND at<=? ORDER BY at,id',
            (since, until),
        )]


def merge_retained_events(progress, durable):
    # A multiset preserves repeated real events, even when timestamps coincide.
    key = lambda event: json.dumps(event, ensure_ascii=False, sort_keys=True)
    result = list(progress)
    remaining = Counter(key(event) for event in result)
    for event in durable:
        identity = key(event)
        if remaining[identity]:
            remaining[identity] -= 1
        else:
            result.append(event)
    return result

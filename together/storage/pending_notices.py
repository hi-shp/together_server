"""수집 기준과 별개로 처리가 끝나지 않은 원문을 보존한다."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit


def notice_identity(url):
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if '/CMS/Board/' in parts.path and query.get('board_seq'):
        return (parts.netloc.lower(), query.get('mgr_seq', [''])[0], query['board_seq'][0])
    return (url,)


class PendingNotices:
    def __init__(self, base_dir):
        root = Path(base_dir) / '.runtime'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / 'pending-notices.json'
        self.lock_path = root / 'pending-notices.lock'

    @contextmanager
    def transaction(self):
        with self.lock_path.open('a') as lock:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'schema': 1, 'pending': [], 'completed': []}
            if state.get('schema') != 1 or not isinstance(state.get('pending'), list):
                raise ValueError('Pending notice journal needs inspection')
            before = json.dumps(state, ensure_ascii=False, sort_keys=True)
            yield state
            if json.dumps(state, ensure_ascii=False, sort_keys=True) != before:
                temp = self.path.with_suffix('.pending')
                with temp.open('w', encoding='utf-8') as handle:
                    os.chmod(temp, 0o600)
                    json.dump(state, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self.path)

    @staticmethod
    def matches(row, board, url):
        return row['board_url'] == board and notice_identity(row['source_url']) == notice_identity(url)

    def add(self, board, url, department):
        with self.transaction() as state:
            if not any(self.matches(row, board, url) for row in state['pending']):
                state['pending'].append({'board_url': board, 'source_url': url, 'department': department, 'created_at': time.time(), 'notifications_started': False})

    def for_board(self, board):
        with self.transaction() as state:
            return [dict(row) for row in state['pending'] if row['board_url'] == board]

    @staticmethod
    def merge(previous, discovered):
        result = []
        identities = set()
        for url in [*(row['source_url'] for row in previous), *discovered]:
            key = notice_identity(url)
            if key not in identities:
                identities.add(key)
                result.append(url)
        return result

    def notifications_started(self, board, url):
        return any(self.matches(row, board, url) and row.get('notifications_started') for row in self.for_board(board))

    def mark_notifications(self, board, url):
        with self.transaction() as state:
            for row in state['pending']:
                if self.matches(row, board, url):
                    row['notifications_started'] = True

    def mark_recovery(self, board, url, reviewed_candidate):
        with self.transaction() as state:
            for row in state['pending']:
                if self.matches(row, board, url):
                    row['reviewed_candidate'] = reviewed_candidate
                    row['notifications_started'] = True

    def recovery_candidate(self, board, url):
        return next((row.get('reviewed_candidate') for row in self.for_board(board) if self.matches(row, board, url)), None)

    def finish(self, board, url, outcome='completed'):
        with self.transaction() as state:
            removed = [row for row in state['pending'] if self.matches(row, board, url)]
            state['pending'] = [row for row in state['pending'] if not self.matches(row, board, url)]
            state['completed'].extend({**row, 'completed_at': time.time(), 'outcome': outcome} for row in removed)
            state['completed'] = state['completed'][-5000:]

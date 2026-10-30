"""학습 프로젝트의 누적 비용 예약. 미확인 요청도 한도에 포함한다."""

from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import sqlite3
import time


class TrainingBudgetExceeded(RuntimeError):
    pass


def microdollars(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid cost")
    return int((amount * 1000000).to_integral_value(rounding=ROUND_CEILING))


class TrainingBudget:
    def __init__(self, path, limit_usd=95):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        limit = microdollars(limit_usd)
        if not 0 < limit <= 95000000:
            raise ValueError("Training authorization is at most USD 95")
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY CHECK(id=1), ceiling INTEGER NOT NULL)")
            db.execute("INSERT OR IGNORE INTO budget VALUES (1,?)", (limit,))
            if db.execute("SELECT ceiling FROM budget").fetchone()[0] != limit:
                raise ValueError("Existing project budget must not change")
            db.execute("CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, reserved INTEGER NOT NULL, charged INTEGER, state TEXT NOT NULL, at REAL NOT NULL)")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            self.path.chmod(0o600)
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def reserve(self, key, maximum_usd):
        maximum = microdollars(maximum_usd)
        if maximum <= 0:
            raise ValueError("A positive upper bound is required")
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM requests WHERE charged>reserved LIMIT 1").fetchone():
                raise TrainingBudgetExceeded("Cost model requires review")
            if db.execute("SELECT 1 FROM requests WHERE key=?", (key,)).fetchone():
                return False  # 완료·미확인 모두 자동 재요청하지 않는다.
            used = db.execute("SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM requests").fetchone()[0]
            ceiling = db.execute("SELECT ceiling FROM budget").fetchone()[0]
            if used + maximum > ceiling:
                raise TrainingBudgetExceeded("Training project budget reached")
            db.execute("INSERT INTO requests VALUES (?,?,NULL,'reserved',?)", (key, maximum, time.time()))
        return True

    def settle(self, key, actual_usd):
        actual = microdollars(actual_usd)
        with self.transaction() as db:
            row = db.execute("SELECT reserved,charged FROM requests WHERE key=?", (key,)).fetchone()
            if not row:
                raise ValueError("Request was not reserved")
            if row[1] is not None and row[1] != actual:
                raise ValueError("Recorded charge cannot be replaced")
            db.execute("UPDATE requests SET charged=?,state='settled' WHERE key=?", (actual, key))
        if actual > row[0]:
            # 실제 비용은 먼저 보존한다. 단가/상한 계산을 재검토하기 전 추가 요청 금지.
            raise TrainingBudgetExceeded("Actual charge exceeded reserved bound")

    def summary(self):
        with self.transaction() as db:
            charged, outstanding, count = db.execute("SELECT COALESCE(SUM(charged),0),COALESCE(SUM(CASE WHEN charged IS NULL THEN reserved ELSE 0 END),0),COUNT(*) FROM requests").fetchone()
            return {"charged_usd": charged / 1e6, "unconfirmed_reserved_usd": outstanding / 1e6, "requests": count,
                    "limit_usd": db.execute("SELECT ceiling FROM budget").fetchone()[0] / 1e6}

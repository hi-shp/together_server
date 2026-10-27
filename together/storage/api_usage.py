"""API 호출의 실제 토큰과 예상 비용을 기록하고 같은 요청을 재사용한다."""

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

RATES = {
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-mini-2026-03-17": (0.75, 0.075, 4.50),
}
PRICING_SOURCE = "https://developers.openai.com/api/docs/models/gpt-5.4-mini"
PRICING_CHECKED = "2026-10-26"


class DailyApiBudgetExceeded(RuntimeError):
    pass


class ApiAccounting:
    def __init__(self, base_dir):
        self.path = Path(base_dir) / ".runtime/api-usage.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        os.chmod(self.path, 0o600)
        db.execute(
            "CREATE TABLE IF NOT EXISTS usage (at REAL,day TEXT,model TEXT,task TEXT,input INTEGER,output INTEGER,cached INTEGER,cost REAL,error TEXT)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY,expires REAL,result TEXT)"
        )
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def key(self, model, request):
        return hashlib.sha256(
            json.dumps([model, request], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def cached(self, model, request):
        with self.connection() as db:
            row = db.execute(
                "SELECT result FROM cache WHERE key=? AND expires>?",
                (self.key(model, request), time.time()),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def check_budget(self):
        day = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        with self.connection() as db:
            used = db.execute(
                "SELECT COALESCE(SUM(cost),0) FROM usage WHERE day=?", (day,)
            ).fetchone()[0]
        limit = float(os.getenv("GPT_DAILY_BUDGET_USD", "0.25"))
        if limit > 0 and used + 0.005 > limit:
            raise DailyApiBudgetExceeded("Daily API budget reached")

    def record(self, model, request, response=None, error=None):
        task = (
            request.get("response_format", {})
            .get("json_schema", {})
            .get("name", "unknown")
        )
        task = (
            "crawler:"
            if os.getenv("API_USAGE_CONTEXT") == "crawler"
            else "verification:"
        ) + task
        usage = getattr(response, "usage", None)
        inp = getattr(usage, "prompt_tokens", 0) or 0
        out = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        if not isinstance(cached, int):
            cached = 0
        rates = RATES.get(model)
        cost = (
            ((inp - cached) * rates[0] + cached * rates[1] + out * rates[2]) / 1e6
            if rates
            else None
        )
        error_code = ""
        if error:
            body = getattr(error, "body", {}) or {}
            if not isinstance(body, dict):
                body = {}
            error_code = body.get("code") or type(error).__name__
            if not isinstance(error_code, str):
                error_code = type(error).__name__
        with self.connection() as db:
            db.execute(
                "INSERT INTO usage VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    time.time(),
                    datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),
                    model,
                    task,
                    inp,
                    out,
                    cached,
                    cost,
                    error_code,
                ),
            )
            db.execute("DELETE FROM usage WHERE at<?", (time.time() - 180 * 86400,))

    def store(self, model, request, result):
        task = request.get("response_format", {}).get("json_schema", {}).get("name", "")
        ttl = 3600 if "duplicate" in task else 7 * 86400
        with self.connection() as db:
            db.execute("DELETE FROM cache WHERE expires<?", (time.time(),))
            db.execute(
                "INSERT OR REPLACE INTO cache VALUES (?,?,?)",
                (
                    self.key(model, request),
                    time.time() + ttl,
                    json.dumps(result, ensure_ascii=False),
                ),
            )
            db.execute(
                "DELETE FROM cache WHERE key IN (SELECT key FROM cache ORDER BY expires DESC LIMIT -1 OFFSET 5000)"
            )

    def summary(self):
        with self.connection() as db:
            rows = db.execute(
                "SELECT day,SUM(CASE WHEN error='DailyApiBudgetExceeded' THEN 0 ELSE 1 END),SUM(input),SUM(output),SUM(cached),SUM(cost) FROM usage WHERE at>? GROUP BY day ORDER BY day",
                (time.time() - 30 * 86400,),
            ).fetchall()
            models = (
                os.getenv("GPT_CATEGORY_MODEL") or "gpt-5.4-mini-2026-03-17",
                os.getenv("GPT_DUPLICATE_MODEL") or "gpt-5.4-mini-2026-03-17",
            )
            errors = db.execute(
                "SELECT error,COUNT(*) FROM usage u WHERE at>? AND error!='' AND model IN (?,?) AND at>COALESCE((SELECT MAX(at) FROM usage s WHERE s.model=u.model AND s.error=''),0) GROUP BY error",
                (time.time() - 86400, *models),
            ).fetchall()
        return {
            "days": [
                dict(
                    zip(
                        (
                            "day",
                            "requests",
                            "input_tokens",
                            "output_tokens",
                            "cached_tokens",
                            "estimated_usd",
                        ),
                        row,
                    )
                )
                for row in rows
            ],
            "errors_last_day": dict(errors),
            "pricing_source": PRICING_SOURCE,
            "pricing_checked": PRICING_CHECKED,
            "scope": "crawler_calls_since_instrumentation",
            "account_balance_is_separate": True,
        }

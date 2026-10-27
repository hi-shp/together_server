"""이메일 발송 요청과 완료 기록을 보관한다."""

from contextlib import contextmanager
import hashlib, json, os, sqlite3, time
from pathlib import Path


class EmailOutbox:
    def __init__(self, base_dir):
        self.path = Path(base_dir) / ".runtime/email-outbox.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,payload TEXT,state TEXT,created REAL,updated REAL,attempts INTEGER,ready REAL,recipients INTEGER,error TEXT)"
            )
            db.execute(
                "DELETE FROM jobs WHERE state='sent' AND updated<?",
                (time.time() - 90 * 86400,),
            )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        os.chmod(self.path, 0o600)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def enqueue(self, payload):
        key = hashlib.sha256(("email|" + payload["event_key"]).encode()).hexdigest()
        now = time.time()
        with self.connection() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    json.dumps(payload, ensure_ascii=False),
                    "pending",
                    now,
                    now,
                    0,
                    now,
                    payload.get("accepted_recipients", len(payload["recipients"])),
                    "",
                ),
            )
            row = db.execute(
                "SELECT id,state,recipients FROM jobs WHERE id=?", (key,)
            ).fetchone()
        result = dict(row)
        result["new"] = inserted.rowcount == 1
        return result

    def recover_interrupted(self):
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET state='unknown',ready=? WHERE state='sending'",
                (time.time(),),
            )

    def claim(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM jobs WHERE state IN ('pending','unknown') AND ready<=? ORDER BY created LIMIT 1",
                (time.time(),),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET state='sending',updated=?,attempts=attempts+1 WHERE id=?",
                (time.time(), row["id"]),
            )
            value = dict(row)
            value["payload"] = json.loads(row["payload"])
            return value

    def complete(self, job, count):
        payload = job["payload"]
        message = f"{payload['department']}/이메일 전송 완료 ({count}명)"
        now = time.time()
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET state='sent',payload='{}',updated=?,recipients=?,error='' WHERE id=?",
                (now, count, job["id"]),
            )
        return message

    def defer(self, job, unknown, error):
        attempts = job["attempts"] + 1
        state = "unknown" if unknown else "pending" if attempts < 3 else "blocked"
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET state=?,updated=?,ready=?,error=? WHERE id=?",
                (state, time.time(), time.time() + 60, error, job["id"]),
            )

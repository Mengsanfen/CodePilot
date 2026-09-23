from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager

from agent.store.sqlite_store import LocalSqliteStore, utc_now

ACTIVE = ("queued", "running", "cancelling")
TERMINAL = ("completed", "failed", "cancelled", "interrupted")


class RunConflict(ValueError):
    pass


class LeaseLost(RuntimeError):
    pass


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RunStore(LocalSqliteStore):
    """Database-authoritative queue with fencing and atomic state/event commits.

    Redis accelerates delivery; a lost wakeup cannot lose a persisted task.
    Expired execution is interrupted, never blindly replayed across side effects.
    """

    def __init__(self, target):
        super().__init__(target)
        sequence = "BIGSERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        self._conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS execution_runs (
                run_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, repo_url TEXT NOT NULL,
                prompt TEXT NOT NULL, status TEXT NOT NULL, request_key TEXT UNIQUE,
                request_hash TEXT NOT NULL, owner TEXT, lease_token TEXT,
                lease_until DOUBLE PRECISION, cancel_requested INTEGER NOT NULL DEFAULT 0,
                attempt INTEGER NOT NULL DEFAULT 0, execution_context TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT,
                resume_checkpoint INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_thread
                ON execution_runs(thread_id) WHERE status IN ('queued','running','cancelling');
            CREATE INDEX IF NOT EXISTS execution_queue ON execution_runs(status, created_at);
            CREATE TABLE IF NOT EXISTS execution_events (
                seq {sequence}, run_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                event TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS execution_event_cursor ON execution_events(run_id, seq);
            CREATE TABLE IF NOT EXISTS external_operations (
                operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, status TEXT NOT NULL,
                result TEXT, created_at TEXT NOT NULL
            );
        """)
        self._conn.commit()

    @contextmanager
    def transaction(self):
        with self._lock:
            self._conn.execute("BEGIN" if self.is_postgres else "BEGIN IMMEDIATE")
            try:
                yield
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def _time(self) -> float:
        sql = ("SELECT EXTRACT(EPOCH FROM clock_timestamp()) AS now" if self.is_postgres
               else "SELECT (julianday('now') - 2440587.5) * 86400.0 AS now")
        return float(self._conn.execute(sql).fetchone()["now"])

    def _event(self, run, event: str, payload: dict):
        self._conn.execute(
            "INSERT INTO execution_events(run_id,thread_id,event,payload,created_at) VALUES (?,?,?,?,?)",
            (run["run_id"], run["thread_id"], event, encode(payload), utc_now()),
        )

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def latest(self, thread_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM execution_runs WHERE thread_id=? ORDER BY created_at DESC LIMIT 1", (thread_id,)
            ).fetchone()
            return dict(row) if row else None

    def submit(self, *, repo_url: str, prompt: str, thread_id: str | None = None,
               request_key: str | None = None) -> dict:
        fingerprint = hashlib.sha256(encode([repo_url, prompt, thread_id]).encode()).hexdigest()
        with self.transaction():
            if request_key:
                existing = self._conn.execute("SELECT * FROM execution_runs WHERE request_key=?", (request_key,)).fetchone()
                if existing:
                    if existing["request_hash"] != fingerprint:
                        raise RunConflict("Idempotency-Key 已用于不同请求")
                    return dict(existing)
            thread_id = thread_id or str(uuid.uuid4())
            existing_thread = self.get_thread(thread_id)
            if existing_thread and existing_thread["repo_url"] != repo_url:
                raise RunConflict("同一会话不能切换仓库，请新建会话")
            if self._conn.execute("""SELECT run_id FROM execution_runs WHERE thread_id=?
                AND status IN ('queued','running','cancelling')""", (thread_id,)).fetchone():
                raise RunConflict("该会话已有排队或运行中的任务")
            run_id, now = str(uuid.uuid4()), utc_now()
            self._conn.execute("""INSERT INTO execution_runs
                (run_id,thread_id,repo_url,prompt,status,request_key,request_hash,created_at,updated_at)
                VALUES (?,?,?,?,'queued',?,?,?,?)""",
                (run_id, thread_id, repo_url, prompt, request_key, fingerprint, now, now))
            self._conn.execute("""INSERT INTO threads
                (thread_id,title,user_prompt,repo_url,latest_run_status,created_at,updated_at)
                VALUES (?,?,?,?,'queued',?,?) ON CONFLICT(thread_id) DO UPDATE SET
                user_prompt=excluded.user_prompt,latest_run_status='queued',updated_at=excluded.updated_at""",
                (thread_id, prompt[:80], prompt, repo_url, now, now))
            run = self.get_run(run_id)
            self._event(run, "run.queued", {"status": "queued"})
            return run

    def claim(self, owner: str, lease_seconds: float = 30) -> dict | None:
        with self.transaction():
            suffix = " FOR UPDATE SKIP LOCKED" if self.is_postgres else ""
            row = self._conn.execute(
                "SELECT * FROM execution_runs WHERE status='queued' ORDER BY created_at LIMIT 1" + suffix
            ).fetchone()
            if not row:
                return None
            run, token = dict(row), str(uuid.uuid4())
            self._conn.execute("""UPDATE execution_runs SET status='running',owner=?,lease_token=?,
                lease_until=?,attempt=attempt+1,updated_at=? WHERE run_id=? AND status='queued'""",
                (owner, token, self._time() + lease_seconds, utc_now(), run["run_id"]))
            self._event(run, "run.started", {"status": "running", "attempt": run["attempt"] + 1})
            return self.get_run(run["run_id"])

    def assert_lease(self, run_id: str, token: str, *, allow_cancel: bool = False) -> dict:
        run = self.get_run(run_id)
        if (not run or run["lease_token"] != token or run["status"] not in ("running", "cancelling")
                or (run["lease_until"] or 0) <= self._time()):
            raise LeaseLost("运行租约已失效")
        if run["cancel_requested"] and not allow_cancel:
            raise LeaseLost("运行已请求取消")
        return run

    def heartbeat(self, run_id: str, token: str, lease_seconds: float = 30) -> dict:
        with self.transaction():
            run = self.assert_lease(run_id, token, allow_cancel=True)
            self._conn.execute("UPDATE execution_runs SET lease_until=? WHERE run_id=? AND lease_token=?",
                               (self._time() + lease_seconds, run_id, token))
            return run

    def finish(self, run_id: str, token: str, status: str, error: str | None = None):
        if status not in TERMINAL:
            raise ValueError("invalid terminal status")
        with self.transaction():
            run = self.assert_lease(run_id, token, allow_cancel=True)
            status = "cancelled" if run["cancel_requested"] else status
            self._conn.execute("""UPDATE execution_runs SET status=?,error=?,lease_until=NULL,updated_at=?
                WHERE run_id=? AND lease_token=?""", (status, error, utc_now(), run_id, token))
            self._conn.execute("UPDATE threads SET latest_run_status=?,updated_at=? WHERE thread_id=?",
                               (status, utc_now(), run["thread_id"]))
            self._event(run, "run." + status, {"status": status, "error": error})

    def cancel(self, run_id: str) -> dict:
        with self.transaction():
            run = self.get_run(run_id)
            if not run:
                raise KeyError(run_id)
            if run["status"] in TERMINAL:
                return run
            status = "cancelled" if run["status"] == "queued" else "cancelling"
            self._conn.execute("UPDATE execution_runs SET status=?,cancel_requested=1,updated_at=? WHERE run_id=?",
                               (status, utc_now(), run_id))
            self._conn.execute("UPDATE threads SET latest_run_status=?,updated_at=? WHERE thread_id=?",
                               (status, utc_now(), run["thread_id"]))
            self._event(run, "run." + status, {"status": status})
            return self.get_run(run_id)

    def recover_expired(self) -> int:
        with self.transaction():
            rows = self._conn.execute("""SELECT * FROM execution_runs
                WHERE status IN ('running','cancelling') AND lease_until < ?""", (self._time(),)).fetchall()
            for run in rows:
                status = "cancelled" if run["cancel_requested"] else "interrupted"
                error = "Worker 租约过期；请核对外部操作后显式恢复"
                self._conn.execute("UPDATE execution_runs SET status=?,error=?,updated_at=? WHERE run_id=?",
                                   (status, error, utc_now(), run["run_id"]))
                self._conn.execute("UPDATE threads SET latest_run_status=?,updated_at=? WHERE thread_id=?",
                                   (status, utc_now(), run["thread_id"]))
                self._event(run, "run." + status, {"status": status, "error": error})
            return len(rows)

    def append_event(self, run_id: str, token: str, event: str, payload: dict):
        with self.transaction():
            run = self.assert_lease(run_id, token)
            self._event(run, event, payload)

    def events(self, run_id: str, after: int = 0, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("""SELECT * FROM execution_events WHERE run_id=? AND seq>?
                ORDER BY seq LIMIT ?""", (run_id, after, min(limit, 1000))).fetchall()
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def set_context(self, run_id: str, token: str, context: dict):
        with self.transaction():
            self.assert_lease(run_id, token)
            self._conn.execute("UPDATE execution_runs SET execution_context=? WHERE run_id=?",
                               (encode(context), run_id))

    def resume(self, run_id: str) -> dict:
        with self.transaction():
            run = self.get_run(run_id)
            if not run:
                raise KeyError(run_id)
            if run["status"] != "interrupted" or not run["execution_context"]:
                raise RunConflict("仅可恢复有 Checkpoint 执行上下文的 interrupted 任务")
            if (run["lease_until"] or 0) + 5 > self._time():
                raise RunConflict("等待旧进程看门狗停止后再恢复")
            if self._conn.execute("""SELECT run_id FROM execution_runs WHERE thread_id=?
                AND status IN ('queued','running','cancelling')""", (run["thread_id"],)).fetchone():
                raise RunConflict("会话已有活动任务")
            self._conn.execute("""UPDATE execution_runs SET status='queued',resume_checkpoint=1,
                cancel_requested=0,error=NULL,updated_at=? WHERE run_id=?""", (utc_now(), run_id))
            self._event(run, "run.resuming", {"status": "queued"})
            return self.get_run(run_id)

    def operation_start(self, run_id: str, token: str, operation_id: str) -> dict | None:
        with self.transaction():
            self.assert_lease(run_id, token)
            row = self._conn.execute("SELECT * FROM external_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row:
                if row["status"] == "completed":
                    return json.loads(row["result"])
                raise RunConflict("外部操作结果不确定，必须核对远端结果，禁止自动重复发布")
            self._conn.execute("INSERT INTO external_operations VALUES (?,?, 'pending',NULL,?)",
                               (operation_id, run_id, utc_now()))
            return None

    def operation_finish(self, run_id: str, token: str, operation_id: str, result: dict):
        with self.transaction():
            self.assert_lease(run_id, token)
            self._conn.execute("UPDATE external_operations SET status='completed',result=? WHERE operation_id=? AND run_id=?",
                               (encode(result), operation_id, run_id))

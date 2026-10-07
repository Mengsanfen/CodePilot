"""Immutable external-operation proposals; approval is outside the agent tool surface."""
from __future__ import annotations

import hashlib
import json

from agent.orchestration.store import encode, RunConflict
from agent.store.sqlite_store import utc_now


def setup(store):
    store._conn.execute('''CREATE TABLE IF NOT EXISTS operation_approvals (
        operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL,
        parameters TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL)''')
    store._conn.commit()


def propose(store, run_id: str, token: str, kind: str, parameters: dict) -> dict:
    operation_id = hashlib.sha256(encode([run_id, kind, parameters]).encode()).hexdigest()
    with store.transaction():
        run = store.assert_lease(run_id, token)
        store._conn.execute('''INSERT INTO operation_approvals VALUES (?,?,?,?,'pending',NULL,?)
            ON CONFLICT(operation_id) DO NOTHING''', (operation_id, run_id, kind, encode(parameters), utc_now()))
        row = dict(store._conn.execute('SELECT * FROM operation_approvals WHERE operation_id=?', (operation_id,)).fetchone())
        store._event(run, 'operation.proposed', {'operation_id': operation_id, 'kind': kind})
        return {'operation_id': operation_id, 'status': row['status'],
                'message': '外部操作尚未执行。请在操作审批 API 查看完整参数并批准。'}


def list_operations(store, run_id: str) -> list[dict]:
    with store._lock:
        rows = store._conn.execute('SELECT * FROM operation_approvals WHERE run_id=? ORDER BY created_at', (run_id,)).fetchall()
        return [{**dict(row), 'parameters': json.loads(row['parameters']),
                 'result': json.loads(row['result']) if row['result'] else None} for row in rows]


def approve(store, operation_id: str, expected_digest: str, action) -> dict:
    if expected_digest != operation_id:
        raise RunConflict('审批指纹不匹配，请重新读取完整操作')
    with store.transaction():
        suffix = ' FOR UPDATE' if store.is_postgres else ''
        row = store._conn.execute('SELECT * FROM operation_approvals WHERE operation_id=?' + suffix, (operation_id,)).fetchone()
        if not row:
            raise KeyError(operation_id)
        if row['status'] == 'completed':
            return json.loads(row['result'])
        if row['status'] != 'pending':
            raise RunConflict('操作结果可能不确定，请人工核对远端；禁止自动重复执行')
        run = store.get_run(row['run_id'])
        if run['status'] != 'completed':
            raise RunConflict('只可批准已完成任务的操作；先等待任务完成')
        store._conn.execute("UPDATE operation_approvals SET status='executing' WHERE operation_id=?", (operation_id,))
    try:
        result = action(row['kind'], json.loads(row['parameters']))
    except Exception:
        with store.transaction():
            store._conn.execute("UPDATE operation_approvals SET status='uncertain' WHERE operation_id=?", (operation_id,))
            store._event(run, 'operation.uncertain', {'operation_id': operation_id})
        raise
    with store.transaction():
        store._conn.execute("UPDATE operation_approvals SET status='completed',result=? WHERE operation_id=?", (encode(result), operation_id))
        if row['kind'] == 'create_pr' and result.get('html_url'):
            store._conn.execute('UPDATE threads SET pr_url=?,branch_name=?,updated_at=? WHERE thread_id=?',
                                (result['html_url'], json.loads(row['parameters']).get('head'), utc_now(), run['thread_id']))
        store._event(run, 'operation.completed', {'operation_id': operation_id})
    return result

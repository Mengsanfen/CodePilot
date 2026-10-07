"""Snapshot-bound review findings and atomic human-authorized repair submissions."""
from __future__ import annotations
import hashlib
import json
from agent.store.sqlite_store import utc_now


def setup(store):
    store._conn.executescript('''
        CREATE TABLE IF NOT EXISTS run_specs (
            run_id TEXT PRIMARY KEY, spec TEXT NOT NULL, trace_context TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS verified_findings (
            finding_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, digest TEXT NOT NULL,
            head_sha TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS finding_decisions (
            finding_id TEXT PRIMARY KEY, decision TEXT NOT NULL, fix_run_id TEXT,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS task_memories (
            run_id TEXT PRIMARY KEY, repo_url TEXT NOT NULL, head_sha TEXT NOT NULL,
            payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS review_evidence (
            run_id TEXT NOT NULL, phase TEXT NOT NULL, payload TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(run_id,phase));
    ''')
    store._conn.commit()


def record_finding(store, run_id, token, snapshot, payload):
    from agent.orchestration.store import encode
    canonical = {**payload, 'title': payload['title'].strip(), 'file': payload['file'].replace('\\', '/')}
    fingerprint = [run_id, snapshot['digest'], canonical['file'], canonical['line'], canonical['title'].casefold()]
    finding_id = 'finding-' + hashlib.sha256(encode(fingerprint).encode()).hexdigest()[:24]
    with store.transaction():
        run = store.assert_lease(run_id, token)
        inserted = store._conn.execute('''INSERT INTO verified_findings VALUES (?,?,?,?,?,?)
            ON CONFLICT(finding_id) DO NOTHING''', (finding_id, run_id, snapshot['digest'], snapshot['head'],
                                                   encode(canonical), utc_now()))
        if inserted.rowcount:
            store._event(run, 'review.finding', {'finding_id': finding_id, 'digest': snapshot['digest']})
    return finding_id


def findings(store, run_id):
    with store._lock:
        rows = store._conn.execute('''SELECT f.*,d.decision,d.fix_run_id FROM verified_findings f
            LEFT JOIN finding_decisions d ON d.finding_id=f.finding_id WHERE f.run_id=?
            ORDER BY f.created_at,f.finding_id''', (run_id,)).fetchall()
        return [{**dict(r), 'payload': json.loads(r['payload'])} for r in rows]


def confirm_repairs(store, run_id, ids, digest, command):
    """Exactly one repair run per selection; acceptance and enqueue commit together."""
    from agent.orchestration.store import RunConflict, encode
    ids = sorted(set(ids))
    if not ids or len(ids) > 50 or not command.strip() or len(command) > 2000:
        raise ValueError('请选择 1–50 条 findings 并提供验收命令（最多 2000 字符）')
    with store.transaction():
        parent = store.get_run(run_id)
        if not parent:
            raise KeyError(run_id)
        if parent['status'] != 'completed':
            raise RunConflict('仅可确认已完成审查的发现')
        chosen = [f for f in findings(store, run_id) if f['finding_id'] in ids]
        if len(chosen) != len(ids) or any(f['digest'] != digest for f in chosen):
            raise RunConflict('审查快照或 finding 不匹配，请刷新后重新确认')
        repair_spec = {'kind': 'repair', 'review_run_id': run_id, 'digest': digest,
                       'head_sha': chosen[0]['head_sha'], 'findings': [f['payload'] for f in chosen],
                       'verification_command': command.strip()}
        key = 'repair:' + hashlib.sha256(encode([run_id, ids, digest, command.strip()]).encode()).hexdigest()
        previous = store._conn.execute('SELECT * FROM execution_runs WHERE request_key=?', (key,)).fetchone()
        if previous:
            return dict(previous)
        if any(f['decision'] for f in chosen):
            raise RunConflict('所选 finding 已处理；请使用后续审查结果开启新一轮')
        child = store.submit(repo_url=parent['repo_url'], thread_id=parent['thread_id'],
            prompt='实施已确认的审查修复，并执行验收命令及独立复审。', request_key=key, spec=repair_spec)
        for finding_id in ids:
            store._conn.execute('INSERT INTO finding_decisions VALUES (?, ?, ?, ?)',
                                (finding_id, 'accepted', child['run_id'], utc_now()))
        store._event(parent, 'review.accepted', {'finding_ids': ids, 'fix_run_id': child['run_id'], 'digest': digest})
        return child

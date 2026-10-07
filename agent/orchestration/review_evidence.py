"""Fenced phase receipts and conservative, snapshot-bound review outcomes."""
from contextlib import contextmanager
from contextvars import ContextVar
import json

from agent.orchestration.store import encode
from agent.store.sqlite_store import utc_now

_review_scope = ContextVar('review_evidence_scope', default=None)


def read(store, run_id, phase):
    with store._lock:
        row = store._conn.execute('SELECT payload FROM review_evidence WHERE run_id=? AND phase=?',
                                  (run_id, phase)).fetchone()
        return json.loads(row['payload']) if row else None


def write(store, run, phase, payload):
    with store.transaction():
        current = store.assert_lease(run['run_id'], run['lease_token'])
        store._conn.execute('''INSERT INTO review_evidence VALUES (?,?,?,?)
            ON CONFLICT(run_id,phase) DO UPDATE SET payload=excluded.payload,created_at=excluded.created_at''',
            (run['run_id'], phase, encode(payload), utc_now()))
        store._event(current, 'review.evidence', {'phase': phase, **payload})


def matches(receipt, snapshot):
    return bool(receipt and receipt.get('digest') == snapshot['digest'] and receipt.get('head') == snapshot['head'])


@contextmanager
def review_scope(phase):
    token = _review_scope.set(phase)
    try:
        yield
    finally:
        _review_scope.reset(token)


def record_read(snapshot):
    from agent.orchestration.context import identity
    from agent.orchestration.service import get_run_store
    current, phase = identity(), _review_scope.get()
    if current and phase:
        write(get_run_store(), {'run_id': current[0], 'lease_token': current[1]}, phase + '.read',
              {key: snapshot[key] for key in ('head', 'digest', 'truncated', 'skipped')})


def finalize(store, run, snapshot, phase, answer, verification=None):
    from agent.orchestration.reviews import findings
    proof = read(store, run['run_id'], phase + '.read')
    reasons = []
    if not matches(proof, snapshot):
        reasons.append('missing_or_stale_diff_read')
    if proof and proof.get('truncated'):
        reasons.append('diff_truncated')
    if snapshot.get('skipped'):
        reasons.append('files_excluded')
    if not answer.strip():
        reasons.append('missing_review_summary')
    if verification is not None and (not matches(verification, snapshot) or verification.get('exit_code') != 0):
        reasons.append('verification_failed_or_stale')
    current = [f for f in findings(store, run['run_id'])
               if f['digest'] == snapshot['digest'] and f['head_sha'] == snapshot['head']]
    payload = {'head': snapshot['head'], 'digest': snapshot['digest'], 'phase': phase,
               'outcome': 'incomplete' if reasons else 'changes_requested' if current else 'no_findings',
               'reasons': reasons, 'finding_ids': [f['finding_id'] for f in current],
               'excluded_files': snapshot.get('skipped', []), 'verification': verification,
               'summary': answer[:4000], 'recorded_at': utc_now()}
    write(store, run, 'report', payload)
    return payload


def verify_once(store, run, root, backend, command):
    """Reuse only a successful check on an identical visible worktree and command.

    A crash before committing its receipt remains uncertain and may re-execute
    on explicit resume; this is not an exactly-once guarantee for shell effects.
    """
    from agent.tools.review_context import diff_snapshot
    from agent.observability import stage
    from agent.security import redact
    before = diff_snapshot(root)
    previous = read(store, run['run_id'], 'verification')
    if matches(previous, before) and previous.get('command') == command and previous.get('exit_code') == 0:
        store.append_event(run['run_id'], run['lease_token'], 'verification.reused',
                           {'digest': before['digest'], 'head': before['head']})
        return previous
    with stage('repair.verify'):
        check = backend.execute(command, timeout=300)
        after = diff_snapshot(root)
        # Bind to inputs, not outputs: a command modifying source cannot attest
        # that its final tree has been checked.
        receipt = {'head': before['head'], 'digest': before['digest'], 'command': command,
                   'exit_code': check.exit_code, 'output': redact(check.output[:4000]),
                   'workspace_changed': not matches(before, after)}
        write(store, run, 'verification', receipt)
        store.append_event(run['run_id'], run['lease_token'], 'verification.result', receipt)
        if check.exit_code != 0:
            raise RuntimeError('修复后的验收命令失败；保留工作区与日志供下一轮处理')
        return receipt

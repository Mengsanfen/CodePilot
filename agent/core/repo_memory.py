"""Content-addressed repository manifests, bounded source evidence, no model memory writes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent.tools.review_context import git_read


def build_manifest(root: Path, cache: Path) -> dict:
    head = git_read(root, 'rev-parse', 'HEAD').strip()
    status = git_read(root, 'status', '--porcelain', '--untracked-files=normal')
    if status:
        # Cached summaries describe committed source only; dirty files must be read on demand.
        return {'head': head, 'dirty': True, 'message': '工作区已修改，按需读取文件，跳过版本缓存'}
    key = hashlib.sha256(('v2:' + git_read(root, 'remote', 'get-url', 'origin') + head).encode()).hexdigest()
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (key + '.json')
    if target.exists():
        return {**json.loads(target.read_text(encoding='utf-8')), 'cache_hit': True}
    tracked = git_read(root, 'ls-files', '-z').split('\0')
    paths = [p for p in tracked if p and not any(part in {'.env', '.git', '.secrets'} for part in Path(p).parts)]
    evidence = []
    reused = 0
    blobs = {}
    for entry in git_read(root, 'ls-tree', '-r', '-z', 'HEAD').split('\0'):
        if '\t' in entry:
            metadata, name = entry.split('\t', 1)
            blobs[name] = metadata.split()[-1]
    names = {'readme.md', 'pyproject.toml', 'package.json', 'makefile', 'pytest.ini', 'agents.md'}
    for path in paths:
        source = root / path
        if source.name.lower() not in names or source.is_symlink() or not source.is_file():
            continue
        blob = cache / ('blob-' + blobs[path] + '.json')
        if blob.exists():
            item = json.loads(blob.read_text(encoding='utf-8'))
            reused += 1
        else:
            with source.open('rb') as stream:
                data = stream.read(3000)
            item = {'blob_sha': blobs[path], 'excerpt_sha256': hashlib.sha256(data).hexdigest(),
                    'excerpt': data.decode('utf-8', errors='replace')}
            _atomic_json(blob, item)
        evidence.append({'path': path, **item})
        if len(evidence) >= 12:
            break
    manifest = {'head': head, 'dirty': False, 'files': paths[:3000], 'total_files': len(paths),
                'test_paths': [p for p in paths if 'test' in p.lower()][:100], 'sources': evidence,
                'sources_reused': reused, 'sources_read': len(evidence) - reused}
    _atomic_json(target, manifest)
    return {**manifest, 'cache_hit': False}


def _atomic_json(target, payload):
    import uuid
    temp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(target)


def save_task_memory(store, run, root):
    from agent.security import redact
    from agent.store.sqlite_store import utc_now
    messages = store.list_thread_messages(run['thread_id'])
    answers = [m for m in messages if m['author'] == 'agent' and m['created_at'] >= run['created_at']]
    head = git_read(root, 'rev-parse', 'HEAD').strip()
    payload = {'run_id': run['run_id'], 'head_sha': head, 'request': redact(run['prompt'][:500]),
               'summary': redact(answers[-1]['content'][:2000]) if answers else '',
               'evidence': {'message_id': answers[-1]['message_id'] if answers else None,
                            'events_endpoint': f"/api/runs/{run['run_id']}/events"}}
    with store.transaction():
        store.assert_lease(run['run_id'], run['lease_token'])
        store._conn.execute('''INSERT INTO task_memories VALUES (?,?,?,?,?) ON CONFLICT(run_id)
            DO UPDATE SET head_sha=excluded.head_sha,payload=excluded.payload''',
            (run['run_id'], run['repo_url'], head, json.dumps(payload, ensure_ascii=False), utc_now()))


def task_history(store, repo_url, current_head, *, exclude_run=None, limit=5):
    with store._lock:
        rows = store._conn.execute('''SELECT m.payload FROM task_memories m JOIN execution_runs r ON r.run_id=m.run_id
            WHERE m.repo_url=? AND r.status='completed' AND m.run_id<>?
            ORDER BY m.created_at DESC LIMIT ?''', (repo_url, exclude_run or '', min(limit, 10))).fetchall()
    return [{**json.loads(row['payload']), 'same_commit': json.loads(row['payload'])['head_sha'] == current_head,
             'authority': 'historical_evidence_only'} for row in rows]

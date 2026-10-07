"""Opt-in real-model review/confirm/repair/verify/re-review against an isolated local Git fixture.

No GitHub writes; PR metadata is synthetic and real Git objects are pre-positioned.
Remote PR identity/ref pinning is covered separately in test_review_workflow.py.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required for billed model calls')
    from agent.env_utils import load_environment
    load_environment()
    output = Path('test-results') / ('review-flow-' + str(int(time.time())))
    output = output.resolve()
    base = output / 'workspace'
    output.mkdir(parents=True)
    os.environ.update(CODEPILOT_DATABASE_URL=str(output / 'store.sqlite'),
        CHECKPOINT_DB_PATH=str(output / 'checkpoints.sqlite'), AI_WORKSPACE_ROOT=str(base),
        LX_AICODING_LOG_DIR=str(output / 'logs'), CODEPILOT_EXECUTION_BACKEND='docker',
        CODEPILOT_MODEL_CALL_LIMIT='12', MODEL_MAX_TOKENS='2048', CODEPILOT_LOAD_ENV='false',
        CODEPILOT_EMBEDDED_WORKER='false', CODEPILOT_REDIS_URL='', CODEPILOT_RUN_TIMEOUT='300')
    from agent.backends.worktrees import git, prepare_worktree
    source = output / 'origin'
    source.mkdir()
    git('init', str(source))
    git('-C', str(source), 'config', 'user.name', 'CodePilot Fixture')
    git('-C', str(source), 'config', 'user.email', 'fixture@local')
    git('-C', str(source), 'config', 'core.autocrlf', 'false')
    (source / 'solution.py').write_text("def take(items, count):\n    if count < 0 or count > len(items):\n        raise ValueError('invalid count')\n    return items[:count]\n")
    (source / 'README.md').write_text('# Fixture\nThe take function must accept count == len(items), including empty input.\n')
    git('-C', str(source), 'add', '.')
    git('-C', str(source), 'commit', '-m', 'correct baseline')
    base_sha = git('-C', str(source), 'rev-parse', 'HEAD')
    (source / 'solution.py').write_text((source / 'solution.py').read_text().replace('count > len', 'count >= len'))
    git('-C', str(source), 'commit', '-am', 'fixture regression')
    head = git('-C', str(source), 'rev-parse', 'HEAD')
    from agent.orchestration.service import get_run_store
    from agent.orchestration.worker import Worker
    from agent.app import app
    from fastapi.testclient import TestClient
    from agent.observability import tracer
    store = get_run_store()
    with tracer().start_as_current_span('fixture.submit'):
        run = store.submit(repo_url='https://github.com/example/fixture.git', prompt='Review fixture PR',
            spec={'kind': 'pr_review', 'number': 1, 'base_sha': base_sha, 'head_sha': head})
    workspace = base / 'sessions' / hashlib.sha256(run['thread_id'].encode()).hexdigest()[:24]
    prepare_worktree(base, workspace, str(source), 'fixture', run['thread_id'])
    worker = Worker(store)
    worker.run_once()
    report = {'fixture': 'local synthetic PR; no remote publication', 'review_run_id': run['run_id'],
              'review_status': store.get_run(run['run_id'])['status']}
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    assert report['review_status'] == 'completed', str(output)
    with TestClient(app) as client:
        prefix = f"/api/runs/{run['run_id']}"
        items = client.get(prefix + '/findings').json()
        report['review_report'] = client.get(prefix + '/review-report').json()['report']
        assert report['review_report']['outcome'] == 'changes_requested', report['review_report']
        chosen = [f for f in items if f['payload']['file'] == 'solution.py' and f['payload']['line'] == 2]
        assert chosen, items
        body = {'finding_ids': [f['finding_id'] for f in chosen], 'expected_digest': chosen[0]['digest'],
            'verification_command': 'python -c "import sys; sys.path.insert(0,\'/projects/fixture\'); from solution import take; assert take([1,2],2)==[1,2]; assert take([],0)==[]"'}
        response = client.post(prefix + '/repairs', json=body)
        response.raise_for_status()
        fix = response.json()
        duplicate = client.post(prefix + '/repairs', json=body)
        assert duplicate.json()['run_id'] == fix['run_id']
        worker.run_once()
        metrics = client.get(f"/api/runs/{fix['run_id']}/metrics").json()
        report.update(repair_run_id=fix['run_id'], repair_status=store.get_run(fix['run_id'])['status'],
            review_findings=items, remaining_findings=client.get(f"/api/runs/{fix['run_id']}/findings").json(),
            repair_metrics=metrics, repair_report=client.get(f"/api/runs/{fix['run_id']}/review-report").json()['report'])
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Report:', output / 'report.json')
        assert report['repair_status'] == 'completed'
        assert report['repair_report']['outcome'] == 'no_findings', report['repair_report']
        assert report['repair_report']['verification']['exit_code'] == 0
        assert any(s['stage']=='repair.verify' and s['event']=='stage.completed' for s in metrics['stages'])
        assert any(s['stage']=='repair.re_review' and s['event']=='stage.completed' for s in metrics['stages'])


if __name__ == '__main__':
    main()

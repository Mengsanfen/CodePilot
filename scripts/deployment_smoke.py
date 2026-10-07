"""One read-only task through Nginx -> API -> PostgreSQL -> host Worker -> model."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import httpx
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--request-key', default='deployment-readonly-v1')
parser.add_argument('--start-worker', action='store_true', help='Use only if a host Worker is not already running')
args = parser.parse_args()

root = Path(__file__).resolve().parents[1]
output = root / 'test-results'
output.mkdir(exist_ok=True)
with httpx.Client(base_url='http://127.0.0.1:3000', timeout=15) as client:
    for attempt in range(15):
        try:
            ready = client.get('/dashboard/api/me')
            ready.raise_for_status()
            break
        except httpx.HTTPError:
            if attempt == 14:
                raise
            time.sleep(1)
    homepage = client.get('/')
    homepage.raise_for_status()
    assert 'text/html' in homepage.headers.get('content-type', '')
    assert '<!doctype html>' in homepage.text.lower()
    response = client.post('/api/tasks', headers={'Idempotency-Key': args.request_key}, json={
        'repo_url': 'https://github.com/Mengsanfen/CodePilot.git',
        'prompt': '分析项目：仅阅读根目录 README，用三句话介绍项目，引用实际文件路径。不要调用子 Agent，不修改文件，不执行命令，不发布。'})
    response.raise_for_status()
    run = response.json()
    if run['status'] == 'queued' and args.start_worker:
        env = {**os.environ, 'CODEPILOT_MODEL_CALL_LIMIT': '6', 'MODEL_MAX_TOKENS': '2048', 'CODEPILOT_RUN_TIMEOUT': '300'}
        with (output / 'deployment-worker.log').open('w', encoding='utf-8') as log:
            subprocess.run([sys.executable, 'scripts/compose_worker.py', '--once'], cwd=root, env=env,
                           stdout=log, stderr=subprocess.STDOUT, timeout=330, check=True)
    deadline = time.monotonic() + 330
    while True:
        result = client.get('/api/runs/' + run['run_id']).json()
        if result['status'] not in {'queued', 'running', 'cancelling'} or time.monotonic() > deadline:
            break
        time.sleep(1)
    events = client.get('/api/runs/' + run['run_id'] + '/events').json()
    result['event_count'] = len(events)
    result['via'] = 'nginx -> api -> PostgreSQL/Redis -> Windows Worker -> DeepSeek'
    result['metrics'] = client.get('/api/runs/' + run['run_id'] + '/metrics').json()
    (output / 'deployment-smoke.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
    assert result['status'] == 'completed'

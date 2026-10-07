"""Small real-model read-only acceptance test in a dedicated workspace."""
import json
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.env_utils import load_environment
load_environment()
root = (Path('test-results') / ('smoke-' + str(int(time.time())))).resolve()
root.mkdir(parents=True)
os.environ.update(CODEPILOT_DATABASE_URL=str(root / 'runs.sqlite'),
    LX_AICODING_DATA_DIR=str(root), LX_AICODING_LOG_DIR=str(root / 'logs'),
    AI_WORKSPACE_ROOT=str(root / 'workspace'), CODEPILOT_EMBEDDED_WORKER='false',
    CODEPILOT_MODEL_CALL_LIMIT='6', MODEL_MAX_TOKENS='2048', CODEPILOT_RUN_TIMEOUT='300')
from agent.orchestration.service import get_run_store
from agent.orchestration.worker import Worker
store = get_run_store()
run = store.submit(repo_url='https://github.com/Mengsanfen/CodePilot.git',
    prompt='分析这个仓库：仅阅读根目录 README，使用三句话介绍项目，并给出实际读取的文件路径作为证据。不要调用子 Agent，不修改文件，不运行命令，不发布任何内容。')
Worker(store).run_once()
result = store.get_run(run['run_id'])
events = store.events(run['run_id'], limit=1000)
summary = {'run_id': run['run_id'], 'thread_id': run['thread_id'], 'status': result['status'],
           'error': result['error'], 'event_count': len(events), 'output_directory': str(root)}
messages = store.list_thread_messages(run['thread_id'])
answer = next((m['content'] for m in reversed(messages) if m['author'] == 'agent'), '')
summary['answer_has_readme_evidence'] = 'README' in answer and '只支持 Gitee' not in answer and '是否确认实施' not in answer
(root / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=True))
raise SystemExit(0 if result['status'] == 'completed' and summary['answer_has_readme_evidence'] else 1)

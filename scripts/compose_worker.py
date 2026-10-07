"""Run a host Worker against Compose infrastructure, without exposing Docker's socket to a container."""
import os
import sys
from pathlib import Path
from urllib.parse import quote
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.env_utils import load_environment, get_env
load_environment()
password = quote(get_env('POSTGRES_PASSWORD', 'codepilot-local-only'), safe='')
workspace = Path(get_env('CODEPILOT_HOST_WORKSPACE', 'workspace')).resolve()
workspace.mkdir(parents=True, exist_ok=True)
os.environ.update(CODEPILOT_DATABASE_URL=f'postgresql://codepilot:{password}@127.0.0.1:54329/codepilot',
                  CODEPILOT_REDIS_URL='redis://127.0.0.1:63799/0',
                  CODEPILOT_EXECUTION_BACKEND='docker', AI_WORKSPACE_ROOT=str(workspace),
                  CODEPILOT_EMBEDDED_WORKER='false')
# Host Docker CLI can use the native path directly.
os.environ.pop('CODEPILOT_HOST_WORKSPACE', None)
os.environ['CODEPILOT_LOAD_ENV'] = 'false'
from agent.orchestration.worker import main
main()

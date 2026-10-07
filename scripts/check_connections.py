"""Read-only smoke test. Never prints credentials or model response bodies."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.env_utils import load_environment, get_env
load_environment()
import httpx
with httpx.Client(timeout=30) as client:
    response = client.get(get_env('DEEPSEEK_BASE_URL', 'https://api.deepseek.com').rstrip('/') + '/models',
                          headers={'Authorization': 'Bearer ' + get_env('DEEPSEEK_API_KEY')})
    print('DeepSeek models HTTP:', response.status_code)
    if response.is_success:
        print('Configured model available:', get_env('MAIN_MODEL') in [m['id'] for m in response.json()['data']])
    response = client.get('https://api.github.com/repos/Mengsanfen/CodePilot')
    print('GitHub repository HTTP:', response.status_code)
    if response.is_success:
        print('Default branch:', response.json()['default_branch'])

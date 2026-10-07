"""Credentials scoped to the trusted Git controller, never generated-code shells."""
import base64
from agent.env_utils import get_env
from agent.security import command_environment


def git_environment(provider: str) -> dict[str, str]:
    env = command_environment()
    token = (get_env('GITHUB_TOKEN') or get_env('GH_TOKEN')) if provider == 'github' else get_env('GITEE_TOKEN')
    if token:
        auth = base64.b64encode(('oauth2:' + token).encode()).decode()
        env.update(GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_1=f'http.https://{provider}.com/.extraheader',
                   GIT_CONFIG_VALUE_1='Authorization: Basic ' + auth)
    return env

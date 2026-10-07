"""Assemble coding and independently restricted reviewer agents."""
from __future__ import annotations
import copy
import os
from typing import Any
from deepagents import create_deep_agent
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from agent.backends.local_shell import LocalShellBackend
from agent.core.middleware import SanitizeToolInputsMiddleware, ToolErrorMiddleware
from agent.core.middleware.invalid_tool_calls import InvalidToolCallMiddleware
from agent.core.model import make_main_model
from agent.core.task_intent import is_read_only_task
from agent.prompt import get_system_prompt
from agent.tools import add_review_finding, list_review_findings, open_gitee_pull_request, publish_gitee_pr_comment
from agent.tools.git_control import prepare_git_commit, request_git_push
from agent.tools.review_context import review_local_diff

DEFAULT_RECURSION_LIMIT = 200
MODEL_CALL_RECURSION_LIMIT = int(os.environ.get('CODEPILOT_MODEL_CALL_LIMIT', '30'))
_BACKENDS: dict[str, LocalShellBackend] = {}


def graph_loaded_for_execution(config: dict) -> bool:
    return bool((config.get('configurable') or {}).get('__is_for_execution__'))


def ensure_backend_for_thread(thread_id: str) -> LocalShellBackend:
    if thread_id not in _BACKENDS:
        if os.environ.get('CODEPILOT_EXECUTION_BACKEND') == 'docker':
            from agent.backends.docker import DockerBackend
            backend = DockerBackend()
        else:
            backend = LocalShellBackend()
        _BACKENDS[thread_id] = backend
    return _BACKENDS[thread_id]


def _get_cached_backend(thread_id: str):
    return _BACKENDS[thread_id]


def readonly_subagent(backend, model, *, reviewer: bool):
    readonly = copy.copy(backend)
    readonly.read_only = True
    name = 'reviewer' if reviewer else 'general-purpose'
    prompt = ('你是独立只读代码审查员。先用 review_local_diff 取得当前变更，再读取必要源码。'
              '只报告有源码证据的缺陷，调用 add_review_finding 记录文件、变更行、严重度和风险。'
              '不修改文件、不运行命令、不发布。修复建议由用户确认。' if reviewer else
              '你是只读仓库分析员。使用文件检索，回答主 Agent 提出的问题，并引用实际读取的文件。不要修改或执行。')
    # DeepAgents 0.6 inline SubAgent specs inherit the parent's backend and ignore
    # an arbitrary `backend` key. A separately compiled graph establishes the boundary.
    runnable = create_agent(model=model, system_prompt=prompt,
        tools=[review_local_diff, add_review_finding, list_review_findings] if reviewer else [],
        middleware=[FilesystemMiddleware(backend=readonly),
                    InvalidToolCallMiddleware(),
                    ModelCallLimitMiddleware(run_limit=10, exit_behavior='error'),
                    ToolErrorMiddleware(backend=readonly)])
    return {'name': name, 'description': '独立只读代码审查' if reviewer else '只读分析仓库文件和证据',
            'runnable': runnable}


def get_agent(config: dict[str, Any]):
    config = dict(config or {})
    configurable = dict(config.get('configurable') or {})
    config['configurable'] = configurable
    config.setdefault('recursion_limit', DEFAULT_RECURSION_LIMIT)
    thread_id = configurable.get('thread_id')
    if not thread_id or not graph_loaded_for_execution(config):
        raise ValueError('执行 Agent 必须提供 thread_id 和 __is_for_execution__')
    kind = configurable.get('task_kind', 'qa')
    if kind not in {'coding', 'analysis', 'planning', 'qa', 'sync', 'inspect'}:
        kind = 'qa'
    backend = ensure_backend_for_thread(thread_id)
    backend.read_only = is_read_only_task(kind)
    model = make_main_model()
    from agent.core.graph import get_checkpointer
    tools = [review_local_diff, list_review_findings, add_review_finding]
    if kind == 'coding':
        tools += [prepare_git_commit, request_git_push, open_gitee_pull_request, publish_gitee_pr_comment]
    return create_deep_agent(model=model, tools=tools, system_prompt=get_system_prompt(kind),
        backend=backend,
        subagents=[readonly_subagent(backend, model, reviewer=False), readonly_subagent(backend, model, reviewer=True)],
        middleware=[SanitizeToolInputsMiddleware(backend=backend),
                    InvalidToolCallMiddleware(),
                    ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior='error'),
                    ToolErrorMiddleware(backend=backend)],
        skills=['/skills/'], checkpointer=get_checkpointer()).with_config(config)

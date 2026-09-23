from __future__ import annotations

import logging
from typing import Any

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import RunnableConfig

from agent.backends.local_shell import LocalShellBackend
from agent.core.middleware import SanitizeToolInputsMiddleware, ToolErrorMiddleware
from agent.core.model import make_main_model
from agent.core.task_intent import TaskKind
from agent.prompt import get_system_prompt
from agent.tools import (
    add_review_finding,
    fetch_url,
    list_review_findings,
    open_gitee_pull_request,
    publish_gitee_pr_comment,
    web_search,
)

logger = logging.getLogger(__name__)

DEFAULT_RECURSION_LIMIT = 9999
MODEL_CALL_RECURSION_LIMIT = 5000

# 课程版只保留本地 Windows workspace，不接入 之前项目 的远程 sandbox。
# 这里仍然按照 之前项目 的方式按 thread 缓存 backend，方便同一轮/同一会话复用
# 工作区上下文，也方便后续讲解“thread -> backend -> Agent”的生命周期。
_BACKENDS: dict[str, LocalShellBackend] = {}


def graph_loaded_for_execution(config: RunnableConfig) -> bool:
    """判断当前 Agent 是否用于真实执行。

    之前项目 在 LangGraph Server 中会区分“图结构探测”和“真实运行”。
    LX_AICODING 不使用 langgraph dev，但保留这个判断，可以让课程代码结构
    尽量贴近 之前项目，并避免没有 thread_id 时误创建完整工具链。
    """

    configurable = (config or {}).get("configurable") or {}
    return bool(configurable.get("__is_for_execution__", False))


def _get_cached_backend(thread_id: str) -> LocalShellBackend:
    """读取已经绑定到 thread 的 backend。"""

    backend = _BACKENDS.get(thread_id)
    if backend is None:
        raise RuntimeError(f"没有找到 thread 对应的 LocalShellBackend：{thread_id}")
    return backend


def ensure_backend_for_thread(thread_id: str) -> LocalShellBackend:
    """获取或创建绑定到 thread 的本地 backend。

    这个函数对应 之前项目 的 `ensure_sandbox_for_thread`，但做了功能减法：
    - 不创建远程 sandbox；
    - 不处理 GitHub proxy；
    - 不接入 LangSmith metadata；
    - 只负责复用当前机器上的 `E:\\ai_workspace` 工作区。
    """

    backend = _BACKENDS.get(thread_id)
    if backend is None:
        logger.info("为 thread 创建 LocalShellBackend：%s", thread_id)
        backend = LocalShellBackend()
        _BACKENDS[thread_id] = backend
    else:
        logger.info("复用 thread 的 LocalShellBackend：%s", thread_id)
    return backend


def _general_purpose_subagent(model: BaseChatModel) -> SubAgent:
    """构建 之前项目 风格的通用分析子 Agent。

    子 Agent 只负责阅读、分析、总结和给主 Agent 提供建议。它不能直接修改
    `/projects` 下的源码，也不能改 `/skills`、`/policies`、`/runtimes`。
    这样可以让主 Agent 保持最终执行权，降低子 Agent 误改代码的风险。
    """

    return {
        "name": GENERAL_PURPOSE_SUBAGENT["name"],
        "description": GENERAL_PURPOSE_SUBAGENT["description"],
        "system_prompt": GENERAL_PURPOSE_SUBAGENT["system_prompt"],
        "model": model,
        "permissions": [
            FilesystemPermission(
                operations=["read"],
                paths=[
                    "/projects/**",
                    "/skills/**",
                    "/policies/**",
                    "/reviews/**",
                    "/runtimes/**",
                    "/logs/**",
                    "/tmp/**",
                ],
                mode="allow",
            ),
            FilesystemPermission(
                operations=["write"],
                paths=["/reviews/**", "/tmp/**"],
                mode="allow",
            ),
            FilesystemPermission(
                operations=["write"],
                paths=[
                    "/projects/**",
                    "/skills/**",
                    "/policies/**",
                    "/runtimes/**",
                    "/logs/**",
                ],
                mode="deny",
            ),
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            ),
        ],
    }


def _agent_filesystem_permissions() -> list[FilesystemPermission]:
    """主 Agent 的文件系统权限。

    主 Agent 可以修改 `/projects` 中的 Gitee 项目，也可以写 `/reviews` 和 `/tmp`。
    技能、策略、运行环境和日志目录默认只读，最终边界仍由 LocalShellBackend
    做 Windows 路径校验与写入保护。
    """

    return [
        FilesystemPermission(
            operations=["read"],
            paths=[
                "/projects/**",
                "/skills/**",
                "/policies/**",
                "/reviews/**",
                "/runtimes/**",
                "/logs/**",
                "/tmp/**",
            ],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["write"],
            paths=["/projects/**", "/reviews/**", "/tmp/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["write"],
            paths=["/skills/**", "/policies/**", "/runtimes/**", "/logs/**"],
            mode="deny",
        ),
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/**"],
            mode="deny",
        ),
    ]


def _task_kind_from_config(configurable: dict[str, Any]) -> TaskKind:
    """从 config 中读取任务类型，非法值统一回退为 coding。"""

    value = configurable.get("task_kind", "coding")
    if value in {"coding", "analysis", "planning", "qa", "sync", "inspect"}:
        return value
    return "coding"


def get_agent(config: RunnableConfig):
    """按照 指定 thread 构建 DeepAgent。

    之前项目 的入口是 `async def get_agent(config)`，因为它需要异步解析用户身份、
    远程 sandbox、团队模型配置等。课程版全部使用本地配置，因此这里保留同名
    工厂函数，但实现为同步函数，方便 FastAPI 后台任务直接调用。
    """

    config = dict(config or {})
    configurable = dict(config.get("configurable") or {})
    thread_id = configurable.get("thread_id")
    config["configurable"] = configurable
    config["recursion_limit"] = config.get("recursion_limit", DEFAULT_RECURSION_LIMIT)

    if not isinstance(thread_id, str) or not thread_id or not graph_loaded_for_execution(config):
        # 讲课重点：
        # 有些框架或调试脚本会“探测”Agent 图结构，但这不代表要真正执行任务。
        # 这里返回空 Agent，是为了避免没有 thread_id 时就创建 backend、加载工具、
        # 甚至触发文件系统副作用。真实任务必须带 thread_id 且标记执行态。
        logger.info("没有 thread_id 或不是执行态，返回空 Agent")
        return create_deep_agent(system_prompt="", tools=[]).with_config(config)

    task_kind = _task_kind_from_config(configurable)
    backend = ensure_backend_for_thread(thread_id)

    def backend_factory(_runtime: object, _thread_id: str = thread_id) -> SandboxBackendProtocol:
        # DeepAgents 在运行时会通过 factory 获取 backend。
        # 这里没有每次新建 LocalShellBackend，而是按 thread_id 取缓存对象：
        # - Agent 实例可以按请求重建，拿到最新 prompt/permissions/middleware；
        # - backend 复用同一个工作区和 Git askpass 环境；
        # - checkpoint/store 负责持久状态，不依赖长寿命 Agent 对象。
        return _get_cached_backend(_thread_id)

    # 课程版暂时主 Agent 和子 Agent 共用 deepseek-v4-pro。
    # 后续如果要演示 之前项目 的 profile / fallback / team defaults，
    # 可以从这里拆出 main_model 和 subagent_model 的不同配置。
    main_model = make_main_model()
    subagent_model = make_main_model()

    from agent.core.graph import get_checkpointer

    logger.info("返回带 backend 的 Agent：thread_id=%s task_kind=%s", thread_id, task_kind)
    # 这里是整套 Agent 能力的装配点。讲课时建议从这些参数逐个展开：
    # model 决定推理能力；tools 提供业务动作；system_prompt 提供任务规则；
    # subagents 负责分析委派；backend/permissions 决定文件系统边界；
    # middleware 做参数清洗和异常恢复；skills 提供任务方法论；
    # checkpointer 保存 LangGraph thread state。
    return create_deep_agent(
        model=main_model,
        tools=[
            web_search,
            fetch_url,
            open_gitee_pull_request,
            publish_gitee_pr_comment,
            add_review_finding,
            list_review_findings,
        ],
        system_prompt=get_system_prompt(task_kind),
        subagents=[_general_purpose_subagent(subagent_model)],
        backend=backend_factory,
        permissions=_agent_filesystem_permissions(),
        middleware=[
            SanitizeToolInputsMiddleware(backend=backend),
            ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
            ToolErrorMiddleware(backend=backend),
        ],
        skills=["/skills/"],
        checkpointer=get_checkpointer(),
    ).with_config(config)

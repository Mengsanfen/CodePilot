from __future__ import annotations

from agent.core.persistence import make_checkpointer
from agent.core.settings import CHECKPOINT_DB_PATH, DATABASE_TARGET
from agent.core.task_intent import TaskKind
from agent.store import LocalSqliteStore

_store: LocalSqliteStore | None = None
_checkpointer = None


def get_store() -> LocalSqliteStore:
    """获取业务 SQLite Store。

    Store 保存的是平台业务数据，例如任务列表、仓库地址、PR URL、review findings。
    它和 LangGraph checkpoint 是两套数据：
    - Store 面向页面和业务查询。
    - Checkpoint 面向 Agent 的 thread state 和消息历史恢复。
    """

    global _store
    if _store is None:
        _store = LocalSqliteStore(DATABASE_TARGET)
    return _store


def get_checkpointer():
    """获取 LangGraph SQLite checkpointer。

    checkpointer 负责保存 Agent 运行过程中的 messages、工具调用状态和 thread state。
    本项目显式把 checkpoint 写到 data/checkpoints.sqlite，方便课堂演示重启恢复。
    """

    global _checkpointer
    if _checkpointer is None:
        _checkpointer = make_checkpointer(CHECKPOINT_DB_PATH)
    return _checkpointer


def build_agent(thread_id: str, task_kind: TaskKind = "coding"):
    """构建一个绑定 thread_id 的 DeepAgent 兼容入口。

    真实创建逻辑已经迁移到 `agent.server.get_agent(config)`，这样项目结构更接近
    open-swe 的 LangGraph Server 入口。保留这个函数是为了兼容旧脚本和已有
    FastAPI runtime 调用点。
    """

    from agent.server import get_agent

    return get_agent(
        {
            "configurable": {
                "thread_id": thread_id,
                "task_kind": task_kind,
                "__is_for_execution__": True,
            }
        }
    )

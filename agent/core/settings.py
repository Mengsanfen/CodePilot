from __future__ import annotations

from datetime import date
from pathlib import Path

from agent.env_utils import get_env

# 默认数据路径相对于源码根目录，与启动时的当前目录无关。
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# SQLite 数据目录：默认使用项目内 data/。
# 可通过环境变量覆盖；Checkpoint 与业务 Store 使用独立文件。
DATA_DIR = Path(get_env("CODEPILOT_DATA_DIR", str(PROJECT_ROOT / "data"))).resolve()
CHECKPOINT_DB_PATH = Path(
    get_env("CHECKPOINT_DB_PATH", str(DATA_DIR / "checkpoints.sqlite"))
).resolve()
STORE_DB_PATH = Path(get_env("STORE_DB_PATH", str(DATA_DIR / "store.sqlite"))).resolve()
DATABASE_TARGET = get_env("CODEPILOT_DATABASE_URL") or STORE_DB_PATH

# 日志目录：所有后端日志和 Agent 运行日志都写入项目内 logs/。
# 用于控制台输出和运行问题定位。
LOG_DIR = Path(get_env("CODEPILOT_LOG_DIR", str(PROJECT_ROOT / "logs"))).resolve()
LOG_LEVEL = get_env("CODEPILOT_LOG_LEVEL", "INFO").upper()
LOG_ROTATION_WHEN = get_env("CODEPILOT_LOG_WHEN", "midnight")
LOG_ROTATION_INTERVAL = int(get_env("CODEPILOT_LOG_INTERVAL", "1"))
LOG_RETENTION_DAYS = int(get_env("CODEPILOT_LOG_RETENTION_DAYS", "14"))


def log_date_text(target_date: date | None = None) -> str:
    """返回历史日志文件使用的日期文本。

    TimedRotatingFileHandler 当前写入固定文件名，例如 backend.log；
    历史文件默认追加 YYYY-MM-DD 后缀，例如 backend.log.2026-07-10。
    这个函数保留给日志 API 读取历史日期时使用。
    """

    return (target_date or date.today()).isoformat()


def backend_log_path(target_date: date | None = None) -> Path:
    """返回后端日志路径。

    不传日期时返回当前正在写入的固定文件名；传日期时返回标准轮转后的
    历史文件名。
    """

    if target_date is None:
        return LOG_DIR / "backend.log"
    return LOG_DIR / f"backend.log.{log_date_text(target_date)}"


def agent_log_path(target_date: date | None = None) -> Path:
    """返回 Agent 运行日志路径。"""

    if target_date is None:
        return LOG_DIR / "agent-runs.log"
    return LOG_DIR / f"agent-runs.log.{log_date_text(target_date)}"


BACKEND_LOG_PATH = backend_log_path()
AGENT_LOG_PATH = agent_log_path()

# Agent 操作仓库的工作区，与平台源码分离。
WORKSPACE_ROOT = Path(get_env("AI_WORKSPACE_ROOT", str(PROJECT_ROOT / "workspace"))).resolve()
PROJECTS_DIR = WORKSPACE_ROOT / "projects"

# DeepAgents skills 通过原生 backend route 暴露为 `/skills/`。
SKILLS_DIR = WORKSPACE_ROOT / "skills"

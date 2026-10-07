from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENV = PROJECT_ROOT / ".env"


def _load_non_empty_env(path: Path, *, override: bool) -> None:
    """加载本地非空配置，保留进程显式配置的优先级。"""

    if not path.exists():
        return
    for key, value in dotenv_values(path).items():
        if value is None or value.strip() == "":
            continue
        if override or key not in os.environ or os.environ.get(key, "").strip() == "":
            os.environ[key] = value


def load_environment() -> None:
    """加载项目配置；测试可用 CODEPILOT_LOAD_ENV=false 禁用。"""
    if os.environ.get("CODEPILOT_LOAD_ENV", "true").lower() == "false":
        return
    # Explicit process/Compose configuration wins; child workspaces must not be
    # silently reset to the shared .env workspace on every get_env() call.
    _load_non_empty_env(LOCAL_ENV, override=False)

    # 外部 tracing 必须显式启用，默认不发送运行数据。
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    os.environ.setdefault("LANGCHAIN_API_KEY", "")


def get_env(name: str, default: str = "") -> str:
    """读取环境变量。

    这个函数每次读取前都会调用 load_environment，
    目的是让脚本、测试、Uvicorn 启动入口都能得到一致的配置加载行为。
    """

    load_environment()
    # Temporary compatibility for existing local installations. New names win.
    legacy = {
        'CODEPILOT_DATA_DIR': 'LX_AICODING_DATA_DIR',
        'CODEPILOT_LOG_DIR': 'LX_AICODING_LOG_DIR',
        'CODEPILOT_LOG_LEVEL': 'LX_AICODING_LOG_LEVEL',
        'CODEPILOT_LOG_WHEN': 'LX_AICODING_LOG_WHEN',
        'CODEPILOT_LOG_INTERVAL': 'LX_AICODING_LOG_INTERVAL',
        'CODEPILOT_LOG_RETENTION_DAYS': 'LX_AICODING_LOG_RETENTION_DAYS',
        'CODEPILOT_DEBUG_STREAM_EVENTS': 'LX_AICODING_DEBUG_STREAM_EVENTS',
    }
    return os.environ.get(name, os.environ.get(legacy.get(name, ''), default))


def require_env(name: str) -> str:
    """读取必填环境变量。

    DeepSeek API Key、DeepSeek Base URL、Gitee Token 这类关键配置缺失时，
    应该尽早抛出明确错误，而不是等到模型调用或 Git push 时才失败。
    """

    value = get_env(name).strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

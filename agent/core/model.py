from __future__ import annotations

from langchain_openai import ChatOpenAI
from agent.env_utils import get_env, require_env


def make_main_model() -> ChatOpenAI:
    """DeepSeek endpoint with configurable model and bounded request budgets."""
    from agent.core.telemetry import ModelTelemetry
    return ChatOpenAI(
        callbacks=[ModelTelemetry()], stream_usage=True,
        model=get_env("MAIN_MODEL", "deepseek-v4-pro"),
        temperature=float(get_env("MODEL_TEMPERATURE", "0.2")),
        openai_api_key=require_env("DEEPSEEK_API_KEY"),
        openai_api_base=get_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        max_tokens=int(get_env("MODEL_MAX_TOKENS", "8192")),
        timeout=float(get_env("MODEL_TIMEOUT_SECONDS", "120")),
        max_retries=1, streaming=True,
        extra_body={"thinking": {"type": "disabled"}},
    )

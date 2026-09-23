from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from langchain_core.tools import tool

from agent.core.events import record_event
from agent.env_utils import require_env
from agent.tools.gitee_api import mask_token
from agent.tools.runtime_context import get_runtime_thread_id

logger = logging.getLogger("agent.run.web_search")


@lru_cache(maxsize=1)
def _get_zhipu_client() -> Any:
    """懒加载智谱 SDK 客户端。

    Web 搜索不是后端启动的必要能力，所以不能在模块导入时直接初始化 SDK。
    这样即使本地暂时没有安装 `zai`，FastAPI 也能正常启动；只有 Agent 真正调用
    web_search 时，才返回明确的依赖或密钥错误。
    """

    api_key = require_env("ZHIPU_API_KEY")
    try:
        from zai import ZhipuAiClient

        return ZhipuAiClient(api_key=api_key)
    except (ImportError, AttributeError):
        pass

    try:
        from zhipuai import ZhipuAI
    except ImportError as exc:
        raise RuntimeError("缺少智谱 SDK，请先安装依赖：pip install zhipuai") from exc
    return ZhipuAI(api_key=api_key)


@tool("web_search", parse_docstring=True)
def web_search(query: str) -> str:
    """使用智谱搜狗 Web Search API 进行联网搜索。

    适用于需要外部资料支撑的任务，例如查询最新框架文档、第三方库用法、
    API 变更说明、行业资料或错误信息背景。不要搜索密钥、token、私有仓库内容。

    Args:
        query: 需要搜索的关键词或问题。

    Returns:
        搜索结果摘要文本；失败时返回明确错误信息。
    """

    thread_id = get_runtime_thread_id()
    normalized_query = " ".join((query or "").split())
    if not normalized_query:
        return "搜索失败：query 不能为空"

    if thread_id:
        record_event(
            thread_id,
            f"web_search:{normalized_query[:80]}",
            "联网搜索资料",
            kind="fetch",
            status="in_progress",
            detail=json.dumps({"query": normalized_query}, ensure_ascii=False),
        )
    try:
        client = _get_zhipu_client()
        response = client.web_search.web_search(
            search_engine="search_pro",
            search_query=normalized_query,
            count=3,
            search_recency_filter="noLimit",
        )
        results = getattr(response, "search_result", None) or []
        if not results:
            output = "没有搜索到任何内容。"
        else:
            output = "\n\n".join(
                str(getattr(item, "content", "") or "").strip()
                for item in results
                if str(getattr(item, "content", "") or "").strip()
            )
            if not output:
                output = "搜索结果为空。"
        if thread_id:
            record_event(
                thread_id,
                f"web_search:{normalized_query[:80]}",
                "联网搜索资料",
                kind="fetch",
                status="completed",
                detail=json.dumps(
                    {"query": normalized_query, "result_preview": output[:1200]},
                    ensure_ascii=False,
                ),
            )
        return output
    except Exception as exc:
        error = mask_token(str(exc))
        logger.warning("联网搜索失败：query=%s error=%s", normalized_query, error)
        if thread_id:
            record_event(
                thread_id,
                f"web_search:{normalized_query[:80]}",
                "联网搜索资料",
                kind="fetch",
                status="error",
                detail=json.dumps({"query": normalized_query, "error": error}, ensure_ascii=False),
            )
        return f"搜索失败: {error}"

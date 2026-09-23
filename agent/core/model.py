from __future__ import annotations

from langchain_openai import ChatOpenAI

from agent.env_utils import get_env, require_env

DEEPSEEK_V4_MAX_TOKENS = 25600


def make_main_model() -> ChatOpenAI:
    """创建编码智能体使用的 DeepSeek 模型。

    课程版只保留一个主模型，默认对齐 open-swe 使用的 `deepseek-v4-pro`。
    这里不引入多模型 profile、fallback、路由器等生产级能力，
    让学生先理解“模型配置”和“Agent 编排”之间的关系。

    `thinking: disabled` 与 open-swe 的 DeepSeek 调用方式保持一致，
    避免模型输出额外思考内容影响工具调用和最终回复。
    """

    return ChatOpenAI(
        model=get_env("MAIN_MODEL", "deepseek-v4-pro"),
        temperature=1.1,
        openai_api_key=require_env("DEEPSEEK_API_KEY"),
        openai_api_base=require_env("DEEPSEEK_BASE_URL"),
        max_tokens=DEEPSEEK_V4_MAX_TOKENS,
        streaming=True,
        extra_body={"thinking": {"type": "disabled"}},
    )

    # def make_main_model() -> BaseChatModel:
    #     return init_chat_model(
    #         model=get_env("MAIN_MODEL", "deepseek-v4-pro"),
    #         model_provider="openai",
    #         api_key=require_env("DEEPSEEK_API_KEY"),
    #         base_url=require_env("DEEPSEEK_BASE_URL"),
    #         temperature=1.1,
    #         max_tokens=DEEPSEEK_V4_MAX_TOKENS,
    #         streaming=True,
    #         extra_body={"thinking": {"type": "disabled"}},
    #     )
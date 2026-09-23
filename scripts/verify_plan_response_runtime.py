from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.core import runtime
from agent.core.graph import get_store


class FakeAgent:
    """模拟 DeepAgent，只返回一条技术方案消息。"""


CAPTURED_CONTENTS: list[str] = []


def fake_build_agent_for_runtime(*, thread_id: str, task_kind: str, repo_url: str | None = None):
    del thread_id, task_kind, repo_url
    return FakeAgent()


def fake_run_agent_with_event_stream(*, agent, thread_id: str, content: str, task_kind: str | None = None):
    del agent, thread_id, task_kind
    CAPTURED_CONTENTS.append(content)
    return {
        "messages": [
            {
                "type": "ai",
                "content": "## 技术方案\n\n1. 读取现有数据存储。\n2. 设计 SQLite DAO。",
            },
            {
                "type": "ai",
                "content": "是否确认实施该方案？",
            },
        ]
    }


def main() -> None:
    original_build_agent_for_runtime = runtime._build_agent_for_runtime
    original_runner = runtime.run_agent_with_event_stream
    runtime._build_agent_for_runtime = fake_build_agent_for_runtime
    runtime.run_agent_with_event_stream = fake_run_agent_with_event_stream
    thread_id = f"plan-response-runtime-{uuid4()}"
    try:
        result = runtime.run_plan_response_task(
            repo_url="https://gitee.com/msb-goldbin/ai_coding",
            prompt="我想把这个项目的数据存储改为：sqlite数据库",
            thread_id=thread_id,
        )
        if result["status"] != "completed":
            raise AssertionError(f"方案响应任务应直接 completed，实际：{result['status']}")

        store = get_store()
        if store.list_thread_plans(thread_id):
            raise AssertionError("方案响应任务不应该写入 thread_plans")
        agent_messages = [
            message for message in store.list_thread_messages(thread_id) if message["author"] == "agent"
        ]
        if not agent_messages:
            raise AssertionError("方案响应任务未写入 agent 消息")
        content = agent_messages[-1]["content"]
        if "设计 SQLite DAO" not in content:
            raise AssertionError("方案响应任务应保存完整技术方案，而不是只保存最后一句确认")
        if "是否确认实施该方案" not in content:
            raise AssertionError("方案响应任务未追加确认问题")

        revision_result = runtime.run_agent_task(
            repo_url="https://gitee.com/msb-goldbin/ai_coding",
            prompt="一个用户只能授予一个角色，再生成新的方案",
            thread_id=thread_id,
        )
        if revision_result["status"] != "completed":
            raise AssertionError(f"方案修订任务应 completed，实际：{revision_result['status']}")
        if not any("上一版技术方案" in item and "用户新的修改要求" in item for item in CAPTURED_CONTENTS):
            raise AssertionError("方案修订任务没有把上一版方案和本次修改要求传给模型")

        messages = store.list_thread_messages(thread_id)
        agent_messages = [message for message in messages if message["author"] == "agent"]
        if len(agent_messages) < 2:
            raise AssertionError("方案修订后应该保留旧方案并写入新版方案")
        old_metadata = runtime._message_metadata(agent_messages[-2])
        new_metadata = runtime._message_metadata(agent_messages[-1])
        if old_metadata.get("awaiting_confirmation"):
            raise AssertionError("旧方案被修订后不应继续等待确认")
        if not old_metadata.get("superseded"):
            raise AssertionError("旧方案应标记 superseded")
        if not new_metadata.get("awaiting_confirmation"):
            raise AssertionError("新版方案应等待用户确认")
        if "一个用户只能授予一个角色" not in str(new_metadata.get("source_prompt")):
            raise AssertionError("新版方案 source_prompt 应包含本次修改要求")
        user_messages = [message for message in messages if message["author"] == "user"]
        if len(user_messages) < 2:
            raise AssertionError("后续轮次的用户修订要求也应保存到 thread_messages")
    finally:
        runtime._build_agent_for_runtime = original_build_agent_for_runtime
        runtime.run_agent_with_event_stream = original_runner

    print("plan response runtime verification passed")


if __name__ == "__main__":
    main()

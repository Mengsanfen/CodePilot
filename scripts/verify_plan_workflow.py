from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.api.dashboard_routes import _thread_payload
from agent.store.sqlite_store import LocalSqliteStore


def main() -> None:
    """验证新版技术方案工作流。

    新流程不再把技术方案写入 thread_plans，也不再保存 Markdown 文件。
    技术方案作为普通 agent 消息展示，并通过 metadata 标记为等待用户确认。
    """

    with tempfile.TemporaryDirectory() as tmpdir:
        store = LocalSqliteStore(Path(tmpdir) / "store.sqlite")
        thread_id = "thread-plan-message-verify"
        store.upsert_thread(
            thread_id=thread_id,
            title="把 JSON 存储迁移到 SQLite",
            user_prompt="把 JSON 存储迁移到 SQLite",
            repo_url="https://gitee.com/msb-goldbin/ai_coding.git",
            repo_owner="msb-goldbin",
            repo_name="ai_coding",
            latest_run_status="completed",
        )
        store.add_thread_message(
            message_id="agent-plan-1",
            thread_id=thread_id,
            run_id="run-1",
            author="agent",
            content="## SQLite 迁移技术方案\n\n1. 读取现有 JSON 存储。\n\n是否确认实施该方案？",
            metadata={
                "task_kind": "planning",
                "awaiting_confirmation": True,
                "source_prompt": "把 JSON 存储迁移到 SQLite",
            },
        )

        if store.list_thread_plans(thread_id):
            raise AssertionError("新版技术方案流程不应该写入 thread_plans")

        thread = store.get_thread(thread_id)
        assert thread is not None
        thread["latest_run"] = store.get_latest_run(thread_id)
        thread["run_events"] = store.list_run_events(thread_id)
        thread["messages"] = store.list_thread_messages(thread_id)
        payload = _thread_payload(thread)
        if payload["status"] != "finished":
            raise AssertionError(f"completed 应映射为 finished，实际：{payload['status']}")
        if payload.get("latestPlan") is not None:
            raise AssertionError("Dashboard payload 不应再返回 latestPlan")

        visible_text = "\n".join(
            chunk["text"]
            for message in payload["messages"]
            for chunk in message.get("chunks", [])
            if chunk.get("kind") == "text"
        )
        if "SQLite 迁移技术方案" not in visible_text:
            raise AssertionError("Dashboard payload 未展示技术方案正文")
        if "是否确认实施该方案" not in visible_text:
            raise AssertionError("技术方案末尾必须询问是否确认实施")

        metadata = store.list_thread_messages(thread_id)[0]["metadata"]
        parsed = json.loads(metadata)
        if not parsed.get("awaiting_confirmation"):
            raise AssertionError("方案消息未标记 awaiting_confirmation")
        store._conn.close()

    print("plan workflow verification passed")


if __name__ == "__main__":
    main()

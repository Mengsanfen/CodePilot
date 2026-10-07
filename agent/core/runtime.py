from __future__ import annotations

import logging
import json
from pathlib import Path
import uuid
from typing import Any

from langchain_core.messages import BaseMessage

from agent.backends.local_shell import LocalShellBackend
from agent.backends.workspace import Workspace
from agent.core.events import record_event
from agent.core.graph import get_store
from agent.core.repo_mapping import discover_repo_mapping, save_clone_mapping
from agent.core.settings import PROJECTS_DIR, WORKSPACE_ROOT
from agent.core.streaming_runtime import run_agent_with_event_stream
from agent.core.task_intent import classify_task_kind
from agent.server import get_agent
from agent.tools.gitee_api import mask_token, parse_gitee_repo_url

logger = logging.getLogger("agent.run.runtime")


def _build_agent_for_runtime(
    *,
    thread_id: str,
    task_kind: str,
    repo_url: str | None = None,
):
    """为 FastAPI runtime 构造 open-swe 风格的 Agent config。

    open-swe 由 LangGraph Server 注入 `RunnableConfig`。课程版不用
    langgraph dev，所以在这里显式组装 config，再交给 `agent.server.get_agent`。
    """

    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "task_kind": task_kind,
        "__is_for_execution__": True,
    }
    if repo_url:
        configurable["repo_url"] = repo_url
    return get_agent({"configurable": configurable})


def _message_to_dict(message: Any) -> dict[str, Any]:
    """把 LangChain 消息对象转换为前端和 API 更容易展示的字典。

    DeepAgent 返回的 messages 中既可能有 HumanMessage、AIMessage，
    也可能有工具调用消息。课程版 API 不直接暴露 Python 对象，
    而是统一转换成 type/content 结构。
    """

    if isinstance(message, BaseMessage):
        return {"type": message.type, "content": message.content}
    return {"type": type(message).__name__, "content": str(message)}


def _message_content_to_text(content: Any) -> str:
    """把 LangChain message.content 规整成可展示文本。"""

    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    return str(content).strip() if content is not None else ""


def _extract_final_assistant_text(messages: list[dict[str, Any]]) -> str:
    """提取 DeepAgent 最后一条 assistant/ai 消息作为用户可见回答。"""

    for message in reversed(messages):
        message_type = str(message.get("type") or "").lower()
        if message_type not in {"ai", "assistant"}:
            continue
        text = _message_content_to_text(message.get("content"))
        if text:
            return text
    return ""


def _extract_best_plan_text(messages: list[dict[str, Any]]) -> str:
    """从多条 assistant 消息中提取最完整的技术方案。

    DeepAgents 有时会把“完整方案”和“是否确认实施该方案？”拆成不同 assistant
    消息。如果只取最后一条，前端就只剩确认句。方案任务应优先选择包含方案关键词
    且篇幅最长的 assistant 消息；没有命中时再退回最长 assistant 消息。
    """

    candidates: list[str] = []
    for message in messages:
        message_type = str(message.get("type") or "").lower()
        if message_type not in {"ai", "assistant"}:
            continue
        text = _message_content_to_text(message.get("content"))
        if text:
            candidates.append(text)
    if not candidates:
        return ""

    plan_keywords = ["方案", "技术方案", "实施步骤", "验证方案", "风险", "涉及模块", "数据结构"]
    plan_candidates = [
        text for text in candidates if any(keyword in text for keyword in plan_keywords)
    ]
    selected_pool = plan_candidates or candidates
    return max(selected_pool, key=len).strip()


def _build_agent_user_content(
    *,
    repo_url: str,
    task_kind: str,
    prompt: str,
    approved_plan: str | None = None,
) -> str:
    """构造发送给 DeepAgent 的用户内容，避免只读任务被误导去创建 PR。"""

    if task_kind == "coding":
        plan_instruction = ""
        if approved_plan:
            plan_instruction = f"\n\n用户已经确认以下技术方案，请按该方案实施；如执行中发现必要调整，请在最终总结中说明：\n{approved_plan}"
        task_instruction = (
            "这是开发实现任务。请按系统开发流程完成任务，必要时修改代码、验证，按用户要求提出外部发布申请，等待平台审批。"
            f"{plan_instruction}"
        )
    else:
        task_instruction = (
            "这是只读任务。请使用 write_todos 生成适合该任务的清单；"
            "可以准备并读取仓库，但禁止修改文件、提交、push 或创建 Pull Request；"
            "完成后直接用中文回答用户问题。"
        )
    return (
        f"Git 仓库地址：{repo_url}\n\n"
        f"任务类型：{task_kind}\n\n"
        f"用户任务：\n{prompt}\n\n"
        f"{task_instruction}"
    )


def _build_plan_user_content(
    *,
    repo_url: str,
    prompt: str,
    previous_plan: str | None = None,
    revision_prompt: str | None = None,
) -> str:
    """构造专门用于生成技术方案的只读任务内容。

    技术方案现在作为普通 Agent 回答直接展示在网页中，不再保存到 thread_plans
    或 Markdown 文件。用户确认后，后端会读取上一条方案消息作为实施依据。
    """

    if previous_plan and revision_prompt:
        return (
            f"Git 仓库地址：{repo_url}\n\n"
            f"原始用户需求：\n{prompt}\n\n"
            f"上一版技术方案：\n{previous_plan}\n\n"
            f"用户新的修改要求：\n{revision_prompt}\n\n"
            "请基于上一版方案和新的修改要求，重新输出一份完整的新技术方案。\n"
            "不要只输出差异说明，不要只回答新增部分；必须把修订后的完整方案重新组织出来。\n"
            "请只生成技术方案，不要修改文件、不要提交、不要 push、不要创建 Pull Request。\n"
            "方案必须使用中文 Markdown，建议包含：\n"
            "1. 需求理解\n"
            "2. 涉及模块和需要阅读的文件\n"
            "3. 数据结构、接口或页面变化\n"
            "4. 具体实施步骤\n"
            "5. 验证方案\n"
            "6. 风险点和需要用户确认的事项\n"
            "最后必须单独输出一句：是否确认实施该方案？"
        )

    return (
        f"Git 仓库地址：{repo_url}\n\n"
        f"用户需求：\n{prompt}\n\n"
        "请只生成技术方案，不要修改文件、不要提交、不要 push、不要创建 Pull Request。\n"
        "方案必须使用中文 Markdown，建议包含：\n"
        "1. 需求理解\n"
        "2. 涉及模块和需要阅读的文件\n"
        "3. 数据结构、接口或页面变化\n"
        "4. 具体实施步骤\n"
        "5. 验证方案\n"
        "6. 风险点和需要用户确认的事项\n"
        "最后必须单独输出一句：是否确认实施该方案？"
    )


def _is_approval_prompt(prompt: str) -> bool:
    """判断用户是否在等待方案确认阶段明确要求开始实施。"""

    normalized = " ".join((prompt or "").lower().split())
    approval_phrases = [
        "确认",
        "确认实施",
        "同意",
        "同意方案",
        "按方案实施",
        "开始实施",
        "可以实施",
        "按照方案来",
        "就按这个方案",
        "实施",
    ]
    rejection_phrases = ["不确认", "先不要", "不要实施", "修改方案", "重新设计", "调整方案"]
    return any(phrase in normalized for phrase in approval_phrases) and not any(
        phrase in normalized for phrase in rejection_phrases
    )


def _message_metadata(message: dict[str, Any]) -> dict[str, Any]:
    """解析 thread_messages.metadata。

    SQLite 里为了兼容不同调用方，metadata 可能是 JSON 字符串，也可能已经是 dict。
    这里统一规整，供“确认后实施”读取上一轮技术方案。
    """

    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str) and metadata.strip():
        try:
            parsed = json.loads(metadata)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _latest_confirmable_plan_message(thread_id: str) -> dict[str, Any] | None:
    """读取当前线程最近一条等待确认的技术方案消息。"""

    for message in reversed(get_store().list_thread_messages(thread_id)):
        if message.get("author") != "agent":
            continue
        metadata = _message_metadata(message)
        if metadata.get("task_kind") == "planning" and metadata.get("awaiting_confirmation"):
            content = str(message.get("content") or "").strip()
            if content:
                return message
    return None


def _plan_source_prompt(message: dict[str, Any], fallback: str) -> str:
    """读取方案消息里保存的原始需求。

    首次生成方案时，metadata.source_prompt 保存用户原始需求；方案被多次修订时，
    这里继续沿用上一版 source_prompt，避免只把“再改一下”当成完整开发目标。
    """

    metadata = _message_metadata(message)
    source_prompt = str(metadata.get("source_prompt") or "").strip()
    return source_prompt or fallback


def _revision_source_prompt(*, previous_source_prompt: str, revision_prompt: str) -> str:
    """把原始需求和本次修订要求合并成新版方案的 source_prompt。"""

    revision_prompt = revision_prompt.strip()
    if not revision_prompt:
        return previous_source_prompt
    if "补充/修改要求：" in previous_source_prompt:
        return f"{previous_source_prompt.rstrip()}\n- {revision_prompt}"
    return f"{previous_source_prompt.rstrip()}\n\n补充/修改要求：\n- {revision_prompt}"


def _supersede_plan_message(message: dict[str, Any]) -> None:
    """把上一版待确认方案标记为已被新版方案替代。

    注意这里只更新 metadata，不删除旧消息。这样页面历史里仍能看到旧方案，
    但“确认实施”只会读取最新 awaiting_confirmation=true 的方案。
    """

    metadata = _message_metadata(message)
    metadata["awaiting_confirmation"] = False
    metadata["superseded"] = True
    get_store().add_thread_message(
        message_id=str(message["message_id"]),
        thread_id=str(message["thread_id"]),
        run_id=message.get("run_id"),
        author="agent",
        content=str(message.get("content") or ""),
        metadata=metadata,
    )


def is_pull_only_task(prompt: str) -> bool:
    """判断用户任务是否只是拉取远程仓库代码。

    课程版第一阶段默认目标是“修改代码并创建 PR”，但用户明确说
    pull、拉取、同步远程代码时，不应该继续 commit、push 或创建 PR。
    这里用保守关键词判断，只覆盖非常明确的仓库同步请求。
    """

    normalized = " ".join(prompt.lower().split())
    pull_keywords = ["git pull", " pull", "pull一下", "拉取", "同步远程", "更新远程", "拉一下"]
    change_keywords = ["修改", "新增", "修复", "创建pr", "pull request", "提交", "push", "实现"]
    return any(keyword in normalized for keyword in pull_keywords) and not any(
        keyword in normalized for keyword in change_keywords
    )


def is_workspace_listing_task(prompt: str) -> bool:
    """判断是否只是询问当前工作区有哪些项目。"""

    normalized = " ".join(prompt.lower().split())
    return (
        any(keyword in normalized for keyword in ["有哪些项目", "工作目录", "本地工作", "workspace"])
        and not any(keyword in normalized for keyword in ["修改", "修复", "创建", "提交", "push", "pr"])
    )


def initialize_task_record(
    *,
    repo_url: str,
    prompt: str,
    thread_id: str | None = None,
    record_user_message: bool = True,
) -> str:
    """先创建 dashboard 可见的 thread 记录。

    FastAPI 版本不再像 langgraph dev 那样由 LangGraph 服务直接管理线程。
    前端创建任务时需要马上拿到 thread_id 跳转页面，所以这里先写入业务 Store，
    后台任务再继续执行真正的 Agent 或 git pull。
    """

    thread_id = thread_id or str(uuid.uuid4())
    repo = parse_gitee_repo_url(repo_url)
    get_store().upsert_thread(
        thread_id=thread_id,
        title=prompt[:80] or f"Gitee: {repo.owner}/{repo.repo}",
        user_prompt=prompt,
        repo_url=repo.clone_url,
        repo_owner=repo.owner,
        repo_name=repo.repo,
        latest_run_status="running",
    )
    record_event(thread_id, "created", "任务已创建", status="completed")
    if record_user_message:
        get_store().add_thread_message(
            message_id=f"user:{thread_id}:{uuid.uuid4()}",
            thread_id=thread_id,
            author="user",
            content=prompt,
        )
    return thread_id


def run_workspace_listing_task(*, repo_url: str, prompt: str, thread_id: str | None = None) -> dict[str, Any]:
    """直接列出本地工作区项目，不调用模型。"""

    should_record_user_message = thread_id is None
    thread_id = initialize_task_record(
        repo_url=repo_url,
        prompt=prompt,
        thread_id=thread_id,
        record_user_message=should_record_user_message,
    )
    store = get_store()
    if not store.list_thread_messages(thread_id):
        store.add_thread_message(
            message_id=f"user:{thread_id}:{uuid.uuid4()}",
            thread_id=thread_id,
            author="user",
            content=prompt,
        )
    store.clear_run_events(thread_id)
    run_id = str(uuid.uuid4())
    store.record_run(run_id=run_id, thread_id=thread_id, status="running")
    workspace = Workspace(WORKSPACE_ROOT)
    backend = LocalShellBackend(workspace)
    try:
        record_event(thread_id, "workspace", "定位本地工作区", status="completed", detail=str(WORKSPACE_ROOT))
        record_event(thread_id, "list:projects", "查看项目目录", kind="search", status="in_progress", detail="projects")
        projects = backend.list_files("projects")
        detail = "\n".join(projects) if projects else "projects 目录暂无项目"
        record_event(thread_id, "list:projects", "查看项目目录", kind="search", status="completed", detail=detail)
        store.update_thread_status(thread_id, "completed")
        store.record_run(run_id=run_id, thread_id=thread_id, status="completed", finished=True)
        answer = "当前本地工作区 projects 目录下的项目：\n" + (
            "\n".join(f"- {item}" for item in projects) if projects else "- 暂无项目"
        )
        store.add_thread_message(
            message_id=f"agent:{thread_id}:{run_id}",
            thread_id=thread_id,
            run_id=run_id,
            author="agent",
            content=answer,
            metadata={"task_kind": "inspect"},
        )
        record_event(thread_id, "done", "任务完成", status="completed")
        logger.info("工作区项目查询完成：thread_id=%s projects=%s", thread_id, len(projects))
        return {"thread_id": thread_id, "run_id": run_id, "status": "completed", "projects": projects}
    except Exception as exc:
        store.update_thread_status(thread_id, "failed")
        record_event(thread_id, "failed", "任务失败", status="error", detail=mask_token(str(exc)))
        store.record_run(
            run_id=run_id,
            thread_id=thread_id,
            status="failed",
            error=mask_token(str(exc)),
            finished=True,
        )
        logger.exception("工作区项目查询失败：thread_id=%s run_id=%s error=%s", thread_id, run_id, mask_token(str(exc)))
        raise


def _run_git_with_fetch_head_retry(
    backend: LocalShellBackend,
    command: str,
    *,
    cwd: str,
    timeout: int,
) -> Any:
    """执行 Git 命令，并处理 Windows 下偶发的 FETCH_HEAD 权限异常。

    FETCH_HEAD 是 git fetch/pull 写入的临时状态文件，不是仓库源码。
    如果上一次任务异常中断或 IDE 短暂占用导致 Git 无法打开它，可以删除后重试。
    """

    result = backend.run(command, cwd=cwd, timeout=timeout)
    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    if result.exit_code == 0 or "cannot open .git/fetch_head" not in combined_output:
        return result

    fetch_head = backend.workspace.resolve(Path(cwd) / ".git" / "FETCH_HEAD")
    logger.warning("检测到 FETCH_HEAD 权限异常，准备删除后重试：%s", fetch_head)
    try:
        fetch_head.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("删除 FETCH_HEAD 失败：%s", exc)
        return result
    return backend.run(command, cwd=cwd, timeout=timeout)


def run_pull_only_task(*, repo_url: str, prompt: str, thread_id: str | None = None) -> dict[str, Any]:
    """执行只拉取远程代码的轻量任务。

    这个分支不调用大模型，也不创建 PR。它只确保 Gitee 仓库在本地工作区存在，
    然后执行 fetch 和 pull，适合用户在前端输入“先把远程代码 pull 一下”的场景。
    """

    should_record_user_message = thread_id is None
    thread_id = initialize_task_record(
        repo_url=repo_url,
        prompt=prompt,
        thread_id=thread_id,
        record_user_message=should_record_user_message,
    )
    store = get_store()
    store.clear_run_events(thread_id)
    run_id = str(uuid.uuid4())
    store.record_run(run_id=run_id, thread_id=thread_id, status="running")
    repo = parse_gitee_repo_url(repo_url)
    workspace = Workspace(WORKSPACE_ROOT)
    backend = LocalShellBackend(workspace)
    mapping = discover_repo_mapping(repo_url=repo.clone_url, workspace=workspace, store=store)
    relative_dir = Path(mapping.project_dir)
    target = workspace.resolve(relative_dir)
    clone_url = repo.clone_url

    try:
        logger.info("开始执行 pull-only 任务：thread_id=%s repo=%s/%s", thread_id, repo.owner, repo.repo)
        record_event(thread_id, "workspace", "准备本地工作区", status="in_progress")
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        if target.exists() and (target / ".git").exists():
            record_event(thread_id, "workspace", "准备本地工作区", status="completed")
            record_event(thread_id, "sync", "同步远程仓库", kind="execute", status="in_progress")
            remote_result = backend.run(f"git remote set-url origin {clone_url}", cwd=str(relative_dir), timeout=60)
            checkout_result = backend.run("git checkout master", cwd=str(relative_dir), timeout=300)
            fetch_result = _run_git_with_fetch_head_retry(
                backend,
                "git fetch --all",
                cwd=str(relative_dir),
                timeout=300,
            )
            pull_result = _run_git_with_fetch_head_retry(
                backend,
                "git pull origin master --ff-only",
                cwd=str(relative_dir),
                timeout=300,
            )
            outputs = [remote_result, checkout_result, fetch_result, pull_result]
        else:
            record_event(thread_id, "workspace", "准备本地工作区", status="completed")
            record_event(thread_id, "sync", "克隆 Gitee 仓库", kind="execute", status="in_progress")
            clone_result = backend.run(f"git clone {clone_url} {target.name}", cwd="projects", timeout=600)
            outputs = [clone_result]

        failed = next((result for result in outputs if result.exit_code != 0), None)
        if failed is not None:
            record_event(thread_id, "sync", "同步远程仓库", kind="execute", status="error")
            raise RuntimeError(f"git pull 失败: {mask_token(failed.stderr or failed.stdout)}")

        save_clone_mapping(
            repo_url=repo.clone_url,
            project_dir=str(relative_dir).replace("\\", "/"),
            local_path=str(target),
            store=store,
            source="clone_created" if mapping.source == "default_clone_path" else mapping.source,
        )
        record_event(thread_id, "sync", "同步远程仓库", kind="execute", status="completed")
        store.update_thread_status(thread_id, "completed")
        store.record_run(run_id=run_id, thread_id=thread_id, status="completed", finished=True)
        record_event(thread_id, "done", "任务完成", status="completed")
        logger.info("pull-only 任务完成：thread_id=%s repo_dir=%s", thread_id, relative_dir)
        return {"thread_id": thread_id, "run_id": run_id, "status": "completed"}
    except Exception as exc:
        store.update_thread_status(thread_id, "failed")
        record_event(thread_id, "failed", "任务失败", status="error", detail=mask_token(str(exc)))
        store.record_run(
            run_id=run_id,
            thread_id=thread_id,
            status="failed",
            error=mask_token(str(exc)),
            finished=True,
        )
        logger.exception("pull-only 任务失败：thread_id=%s run_id=%s error=%s", thread_id, run_id, mask_token(str(exc)))
        raise


def run_plan_response_task(
    *,
    repo_url: str,
    prompt: str,
    thread_id: str | None = None,
    previous_plan_message: dict[str, Any] | None = None,
    revision_prompt: str | None = None,
) -> dict[str, Any]:
    """为编码需求生成技术方案，并把方案作为普通回答直接展示。

    这个流程不再写 thread_plans 表，也不再保存 Markdown 文件。方案正文进入
    thread_messages，用户后续输入“确认”时再读取该消息进入真正 coding 阶段。
    如果传入 previous_plan_message，则表示用户在等待确认阶段提出了补充要求；
    此时会基于上一版方案重新输出完整新版方案，而不是只输出差异。
    """

    thread_id = thread_id or str(uuid.uuid4())
    store = get_store()
    store.clear_run_events(thread_id)
    logger.info("开始生成技术方案：thread_id=%s repo_url=%s", thread_id, repo_url)
    record_event(thread_id, "created", "任务已创建", status="completed")
    record_event(thread_id, "repo", "解析 Gitee 仓库", status="in_progress")
    repo = parse_gitee_repo_url(repo_url)
    record_event(thread_id, "repo", "解析 Gitee 仓库", status="completed")

    store.add_thread_message(
        message_id=f"user:{thread_id}:{uuid.uuid4()}",
        thread_id=thread_id,
        author="user",
        content=revision_prompt or prompt,
        metadata={"task_kind": "planning", "revision": previous_plan_message is not None},
    )
    plan_source_prompt = prompt
    previous_plan_text: str | None = None
    if previous_plan_message is not None:
        previous_plan_text = str(previous_plan_message.get("content") or "").strip()
        plan_source_prompt = _revision_source_prompt(
            previous_source_prompt=_plan_source_prompt(previous_plan_message, prompt),
            revision_prompt=revision_prompt or prompt,
        )
    store.upsert_thread(
        thread_id=thread_id,
        title=(revision_prompt or prompt)[:80] or f"Gitee: {repo.owner}/{repo.repo}",
        user_prompt=plan_source_prompt,
        repo_url=repo.clone_url,
        repo_owner=repo.owner,
        repo_name=repo.repo,
        latest_run_status="running",
    )
    run_id = str(uuid.uuid4())
    store.record_run(run_id=run_id, thread_id=thread_id, status="running")
    record_event(thread_id, "agent", "构建方案生成 Agent", status="in_progress")
    agent = _build_agent_for_runtime(thread_id=thread_id, task_kind="planning", repo_url=repo.clone_url)
    record_event(thread_id, "agent", "构建方案生成 Agent", status="completed")

    try:
        result = run_agent_with_event_stream(
            agent=agent,
            thread_id=thread_id,
            content=_build_plan_user_content(
                repo_url=repo.clone_url,
                prompt=_plan_source_prompt(previous_plan_message, prompt)
                if previous_plan_message is not None
                else prompt,
                previous_plan=previous_plan_text,
                revision_prompt=revision_prompt,
            ),
            task_kind="planning",
        )
        store.finish_open_run_events(thread_id, status="completed")
        messages = result.get("messages", [])
        plan_text = _extract_best_plan_text(messages)
        if not plan_text:
            raise RuntimeError("技术方案生成失败：模型没有返回可用方案")

        if "是否确认实施该方案" not in plan_text:
            plan_text = f"{plan_text.rstrip()}\n\n是否确认实施该方案？"
        if previous_plan_message is not None:
            _supersede_plan_message(previous_plan_message)
        store.add_thread_message(
            message_id=f"agent-plan:{thread_id}:{run_id}",
            thread_id=thread_id,
            run_id=run_id,
            author="agent",
            content=plan_text.strip(),
            metadata={
                "task_kind": "planning",
                "awaiting_confirmation": True,
                "source_prompt": plan_source_prompt,
                "revision_of": previous_plan_message.get("message_id") if previous_plan_message else None,
                "revision_prompt": revision_prompt,
            },
        )
        store.update_thread_status(thread_id, "completed")
        store.record_run(run_id=run_id, thread_id=thread_id, status="completed", finished=True)
        record_event(thread_id, "plan", "技术方案已输出，等待确认", kind="other", status="completed")
        logger.info("技术方案输出完成：thread_id=%s", thread_id)
        return {
            "thread_id": thread_id,
            "run_id": run_id,
            "status": "completed",
        }
    except Exception as exc:
        store.finish_open_run_events(thread_id, status="error")
        store.update_thread_status(thread_id, "failed")
        record_event(thread_id, "failed", "技术方案生成失败", status="error", detail=mask_token(str(exc)))
        store.record_run(
            run_id=run_id,
            thread_id=thread_id,
            status="failed",
            error=mask_token(str(exc)),
            finished=True,
        )
        logger.exception("技术方案生成失败：thread_id=%s run_id=%s error=%s", thread_id, run_id, mask_token(str(exc)))
        raise


def run_agent_task(*, repo_url: str, prompt: str, thread_id: str | None = None) -> dict[str, Any]:
    if is_workspace_listing_task(prompt):
        return run_workspace_listing_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id)

    if is_pull_only_task(prompt):
        return run_pull_only_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id)

    task_kind = classify_task_kind(prompt)
    thread_id = thread_id or str(uuid.uuid4())
    store = get_store()
    existing_thread = store.get_thread(thread_id)
    approved_plan_text: str | None = None
    coding_prompt = prompt

    if existing_thread and _is_approval_prompt(prompt):
        # 讲课重点：
        # “确认实施”不能直接等价于“执行当前这几个字”。
        # 必须先回到当前 thread 的历史消息里，找到最近一条仍在等待确认的技术方案；
        # 再用该方案的 source_prompt 还原用户最初的开发需求，避免把“确认”当作新需求执行。
        plan_message = _latest_confirmable_plan_message(thread_id)
        if plan_message is not None:
            metadata = _message_metadata(plan_message)
            approved_plan_text = str(plan_message.get("content") or "")
            coding_prompt = str(metadata.get("source_prompt") or existing_thread.get("user_prompt") or prompt)
            metadata["awaiting_confirmation"] = False
            metadata["confirmed_at"] = "yes"
            store.add_thread_message(
                message_id=str(plan_message["message_id"]),
                thread_id=thread_id,
                run_id=plan_message.get("run_id"),
                author="agent",
                content=approved_plan_text,
                metadata=metadata,
            )
            task_kind = "coding"
    elif existing_thread:
        # 如果当前会话已经有一版等待确认的技术方案，而用户没有确认实施，
        # 就把这轮输入视为“修改/补充上一版方案”。这样页面会流式输出完整新版方案，
        # 而不是只给一个生硬总结或误进入普通问答分支。
        plan_message = _latest_confirmable_plan_message(thread_id)
        if plan_message is not None and prompt.strip():
            return run_plan_response_task(
                repo_url=repo_url,
                prompt=_plan_source_prompt(plan_message, existing_thread.get("user_prompt") or prompt),
                thread_id=thread_id,
                previous_plan_message=plan_message,
                revision_prompt=prompt,
            )
    if existing_thread and approved_plan_text is None and _is_approval_prompt(prompt):
        # 没有可确认的技术方案时，把“确认”当作普通问题处理，避免误执行旧任务。
        task_kind = classify_task_kind(prompt)

    if approved_plan_text is not None:
        task_kind = "coding"

    if task_kind == "coding" and approved_plan_text is None:
        # 这是本项目“人在回路”的核心控制点：
        # 只要是 coding 请求，且没有找到用户确认过的方案，就先转入 planning。
        # 这个判断在 runtime 层完成，而不是只写在 Prompt 里，目的是把“先方案、再实施”
        # 做成确定性的产品流程，降低 Agent 首轮直接误改代码的风险。
        return run_plan_response_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id)

    get_store().clear_run_events(thread_id)
    if approved_plan_text is not None:
        record_event(thread_id, "plan:approved", "用户已确认技术方案", kind="other", status="completed")
    logger.info("任务开始：thread_id=%s repo_url=%s", thread_id, repo_url)
    record_event(thread_id, "created", "任务已创建", status="completed")
    record_event(thread_id, "repo", "解析 Gitee 仓库", status="in_progress")
    repo = parse_gitee_repo_url(repo_url)
    logger.info("Gitee 仓库解析成功：owner=%s repo=%s", repo.owner, repo.repo)
    record_event(thread_id, "repo", "解析 Gitee 仓库", status="completed")
    store.add_thread_message(
        message_id=f"user:{thread_id}:{uuid.uuid4()}",
        thread_id=thread_id,
        author="user",
        content=prompt,
        metadata={"task_kind": task_kind, "approved_plan": approved_plan_text is not None},
    )
    store.upsert_thread(
        thread_id=thread_id,
        title=coding_prompt[:80] or f"Gitee: {repo.owner}/{repo.repo}",
        user_prompt=coding_prompt,
        repo_url=repo.clone_url,
        repo_owner=repo.owner,
        repo_name=repo.repo,
        latest_run_status="running",
    )
    run_id = str(uuid.uuid4())
    store.record_run(run_id=run_id, thread_id=thread_id, status="running")
    logger.info("业务 Store 已记录运行：thread_id=%s run_id=%s", thread_id, run_id)
    record_event(thread_id, "agent", "构建 Agent 运行图", status="in_progress")
    agent = _build_agent_for_runtime(thread_id=thread_id, task_kind=task_kind, repo_url=repo.clone_url)
    logger.info("Agent 图已构建：thread_id=%s", thread_id)
    record_event(thread_id, "agent", "构建 Agent 运行图", status="completed")
    try:
        logger.info("开始通过官方事件流调用 Agent：thread_id=%s", thread_id)
        # runtime 只负责“决定跑什么”和“最终状态落库”。
        # 运行过程中的 text delta、write_todos、tool call、subagent 事件解析，
        # 统一交给 streaming_runtime.py，避免调度层和事件解析层混在一起。
        result = run_agent_with_event_stream(
            agent=agent,
            thread_id=thread_id,
            content=_build_agent_user_content(
                repo_url=repo.clone_url,
                task_kind=task_kind,
                prompt=coding_prompt,
                approved_plan=approved_plan_text,
            ),
            task_kind=task_kind,
        )
        store.finish_open_run_events(thread_id, status="completed")
        store.update_thread_status(thread_id, "completed")
        store.record_run(run_id=run_id, thread_id=thread_id, status="completed", finished=True)
        record_event(thread_id, "done", "任务完成", status="completed")
        messages = result.get("messages", [])
        final_answer = _extract_final_assistant_text(messages)
        if final_answer:
            store.add_thread_message(
                message_id=f"agent:{thread_id}:{run_id}",
                thread_id=thread_id,
                run_id=run_id,
                author="agent",
                content=final_answer,
                metadata={"task_kind": task_kind},
            )
        logger.info("任务完成：thread_id=%s run_id=%s messages=%s", thread_id, run_id, len(messages))
        return {"thread_id": thread_id, "run_id": run_id, "status": "completed", "messages": messages}
    except Exception as exc:
        store.finish_open_run_events(thread_id, status="error")
        store.update_thread_status(thread_id, "failed")
        record_event(thread_id, "model", "调用 deepseek-v4-pro", status="error")
        record_event(thread_id, "failed", "任务失败", status="error", detail=mask_token(str(exc)))
        store.record_run(
            run_id=run_id,
            thread_id=thread_id,
            status="failed",
            error=mask_token(str(exc)),
            finished=True,
        )
        logger.exception("任务失败：thread_id=%s run_id=%s error=%s", thread_id, run_id, mask_token(str(exc)))
        raise


def get_task(thread_id: str) -> dict[str, Any] | None:
    """读取单个任务摘要，并附带 reviewer findings。"""

    store = get_store()
    thread = store.get_thread(thread_id)
    if thread is None:
        return None
    thread["findings"] = store.list_findings(thread_id)
    thread["latest_run"] = store.get_latest_run(thread_id)
    thread["run_events"] = store.list_run_events(thread_id)
    thread["messages"] = store.list_thread_messages(thread_id)
    from agent.orchestration.service import get_run_store, public_run
    execution = get_run_store().latest(thread_id)
    if execution:
        thread["execution"] = public_run(execution)
        thread["latest_run_status"] = execution["status"]
    return thread


def list_tasks(limit: int = 50) -> list[dict[str, Any]]:
    """读取最近任务列表，供页面展示历史运行记录。"""

    store = get_store()
    threads = store.list_threads(limit=limit)
    for thread in threads:
        thread_id = thread["thread_id"]
        thread["latest_run"] = store.get_latest_run(thread_id)
        thread["run_events"] = store.list_run_events(thread_id)
        thread["messages"] = store.list_thread_messages(thread_id)
        from agent.orchestration.service import get_run_store, public_run
        execution = get_run_store().latest(thread_id)
        if execution:
            thread["execution"] = public_run(execution)
            thread["latest_run_status"] = execution["status"]
    return threads


def delete_task(thread_id: str) -> bool:
    """删除一个 dashboard 会话。"""

    return get_store().delete_thread(thread_id)

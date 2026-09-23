from __future__ import annotations

from typing import Literal

TaskKind = Literal["coding", "analysis", "planning", "qa", "sync", "inspect"]


def _normalize_prompt(prompt: str) -> str:
    """把用户输入规整成便于关键词判断的短文本。

    这里不调用模型做分类，原因是任务类型会影响工具权限。权限边界必须在本地后端
    可预测地收敛，不能依赖模型自由判断。
    """

    return " ".join((prompt or "").lower().split())


def is_pull_only_task(prompt: str) -> bool:
    """判断用户是否只想同步远程仓库代码。"""

    normalized = _normalize_prompt(prompt)
    pull_keywords = ["git pull", " pull", "pull一下", "拉取", "同步远程", "更新远程", "拉一下"]
    change_keywords = ["修改", "新增", "修复", "创建pr", "pull request", "提交", "push", "实现", "开发"]
    return any(keyword in normalized for keyword in pull_keywords) and not any(
        keyword in normalized for keyword in change_keywords
    )


def is_workspace_listing_task(prompt: str) -> bool:
    """判断用户是否只是在询问本地工作区有哪些项目。"""

    normalized = _normalize_prompt(prompt)
    return (
        any(keyword in normalized for keyword in ["有哪些项目", "工作目录", "本地工作", "workspace"])
        and not any(keyword in normalized for keyword in ["修改", "修复", "创建", "提交", "push", "pr"])
    )


def classify_task_kind(prompt: str) -> TaskKind:
    """按用户意图选择 Agent 工作模式。

    coding 是唯一允许写文件、执行命令、commit、push、创建 PR 的模式。其余模式都按
    只读任务处理，最多准备/读取本地仓库，然后输出分析、方案或答案。
    """

    normalized = _normalize_prompt(prompt)
    if is_pull_only_task(prompt):
        return "sync"
    if is_workspace_listing_task(prompt):
        return "inspect"

    planning_keywords = [
        "方案",
        "计划",
        "设计",
        "步骤",
        "怎么做",
        "如何做",
        "先列出",
        "先帮我设计",
        "不要改",
        "由我确认",
    ]
    analysis_keywords = [
        "分析",
        "解析",
        "目录结构",
        "梳理",
        "解释",
        "查看",
        "有哪些",
        "说明",
        "理解",
        "检查一下",
        "帮我看看",
    ]
    qa_keywords = ["为什么", "是什么", "在哪里", "是否", "能不能", "请问"]
    coding_keywords = [
        "修改",
        "修复",
        "新增",
        "增加",
        "实现",
        "开发",
        "改为",
        "改成",
        "改造",
        "迁移",
        "迁移到",
        "切换为",
        "替换为",
        "接入",
        "升级",
        "删除功能",
        "重构",
        "提交",
        "创建pr",
        "pull request",
        "push",
        "运行测试",
    ]

    has_coding = any(keyword in normalized for keyword in coding_keywords)
    has_planning = any(keyword in normalized for keyword in planning_keywords)
    has_analysis = any(keyword in normalized for keyword in analysis_keywords)

    if has_planning and not has_coding:
        return "planning"
    if has_analysis and not has_coding:
        return "analysis"
    if any(keyword in normalized for keyword in qa_keywords) and not has_coding:
        return "qa"
    if has_coding:
        return "coding"
    return "qa"


def is_read_only_task(task_kind: TaskKind) -> bool:
    """只读任务不暴露写文件、命令执行、Git 提交和 PR 工具。"""

    return task_kind in {"analysis", "planning", "qa", "inspect"}

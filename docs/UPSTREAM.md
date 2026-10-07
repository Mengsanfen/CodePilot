# 来源与兼容说明

CodePilot 起点是 `LX_AICoding` 课程项目。原始重建说明保存在 [历史资料](archive/teaching/LX_AICODING_COURSE_REBUILD_PLAN.md)，核心教学材料也保留在同目录。历史材料描述当时方案，不代表当前安全保证。

基础 UI 来自课程提供的 Open SWE Dashboard / open-swe-app 参考代码；仍使用的 `ported/CloudPromptBar`、`DiffView`、`MarkdownTable`、`MessageView` 保留来源注释。当前仓库没有记录该拷贝的精确上游 commit，因此不补造版本号或原创声明。原有许可及署名必须随相关源文件保留，公开分发前需依据实际来源核对许可范围。

DeepAgents 提供工具循环、文件中间件等 Agent 基础；LangGraph 提供图执行与 Checkpoint Saver。项目新增的工程边界包括持久化任务调度、租约 fencing、受限执行、审批控制器、审查证据及评测，不将框架本身称为自研。

用户界面与新配置统一使用 CodePilot。`agent/env_utils.py` 中仍保留七个 `LX_AICODING_*` 环境变量别名，兼容现有本地配置；新变量优先。它们是命名扫描的明确兼容例外，并非遗漏。历史文档中的旧名称不修改。

P0 提交同时纳入此前未提交的改造基线；提交规模不能直接视为本次编写规模。改造范围见 [OWNERSHIP](OWNERSHIP.md)。

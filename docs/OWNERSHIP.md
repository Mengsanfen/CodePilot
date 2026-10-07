# 能力来源与改造边界

| 模块 | 来源 | 当前项目的工程工作 | 可重点讲解 |
| --- | --- | --- | --- |
| 前端基础 UI | 课程模板 / Open SWE 参考 | API 对接、运行控制、审批参数展示、Review Flow；删除无后端实现的入口 | 中：交互与数据状态，不宣称原创 UI 框架 |
| Agent 图与 Checkpoint | DeepAgents / LangGraph | 生命周期接入、Checkpoint 标识固定、恢复前置条件、只读 Reviewer 图 | 高：框架能力如何受调度约束 |
| Worker / Lease | 项目工程改造 | DB Queue、Redis 唤醒、Heartbeat、Fencing、超时与进程树回收 | 高 |
| Docker Sandbox | Docker 隔离机制 + 项目策略 | 禁网、非 root、资源限额、挂载限制、可信控制器保管凭据 | 高：配置保证与边界限制 |
| Git Worktree / SCM | Git 与平台 API | 会话目录、共享镜像锁、固定提交发布、幂等审批与 uncertain | 高 |
| Reviewer | 基础审查工具 + 项目工程改造 | 固定 diff 快照、行号校验、去重、人工确认、验收和独立复审证据 | 高 |
| Eval / Trace | pytest / OpenTelemetry + 项目代码 | 隐藏验收、固定样例、真实用量、事件回放、跨进程 trace 关联 | 高：必须区分工程测试与模型评测 |

“可重点讲解”表示该模块有可查代码与设计，不代表个人已独立掌握或生产使用。框架提供的能力、AI 辅助开发、个人理解与实际实验应在面试中分别说明。

## P1 删除审计

删除无业务后端的 Admin、Usage、Integrations、Cloud Agents 配置与定时任务页面；将配置页改为只读服务配置，Review 页面接入真实审查入口；删除 Slack 连接及无效退出登录按钮。本平台是单操作者环境，不提供账号认证。

以文件路由、router 入口与测试为根，递归解析相对及 `@/` 静态导入，再用引用搜索确认孤立模块。移除未被使用的旧 ChatView、PromptBar、SourceControl、ThreadPicker 等组件及仅服务这些组件的 UI 元件；保留任务页正在使用的 ported 组件与来源注释。删除名单可以从 P1 提交的 `git show --name-status` 复核。

移除已删除模块的 TypeScript 排除名单；保留上游仍有 `@ts-nocheck` 的组件，严格类型覆盖尚不完整。浏览器旧主题、侧栏与草稿存储键改名，旧值不自动迁移；服务端历史任务及数据库不受影响。

# CodePilot 目标对标与验收

更新：2026-09-26。范围为 Windows 开发、Docker Compose 基础服务及主机 Worker 的单操作者研发平台。以下功能已经实现；生产规模和通用模型成功率不在本次验收范围。

本轮增加快照绑定的审查结论与阶段恢复回执，修复未跟踪文件遗漏及敏感 diff 过滤回退，详见 [审查证据与恢复语义](REVIEW_EVIDENCE.md)。`completed` 与审查通过分开，审查覆盖不全时明确返回 `incomplete`。

## 五项目标对应实现

| 目标 | 实现与入口 | 验证证据及边界 |
| --- | --- | --- |
| 长任务执行 | `agent/orchestration/`：API/Worker 分离，数据库队列、Redis Streams 消费组唤醒、租约 fencing、幂等提交、Checkpoint 显式恢复、取消/超时、外部操作台账 | 真实 PostgreSQL 并发领取、Redis 重投递、进程树清理和恢复测试通过。硬超时有上下文时可恢复，默认最多 3 次。外部效果未知时禁止自动重复发布，不承诺端到端 exactly-once。 |
| 执行隔离 | `agent/backends/`：独立会话 worktree、非 root/禁网/只读根文件系统/资源受限容器；Git/SCM 可信控制器持有凭据；不可变参数审批 | 真实 Docker 隔离测试通过。连续对话共用该会话 worktree 以保留改动，不是每次消息创建空目录。模型密钥、Git 凭据不注入生成代码容器。 |
| 实现与审查协作 | `/api/reviews`、`/findings`、`/repairs`，首页 PR 入口和任务页确认面板；固定 PR base/head、merge-base diff、finding 定位/去重、确认与入队同事务、修复前快照校验、容器验收及独立复审 | 真实 DeepSeek + 本地 Git 样例跑通完整链路；重复确认返回同一修复任务，验收通过且复审无剩余发现。GitHub 元数据通过 HTTP 模拟验证，PR refs 拉取/版本变化拒绝通过真实本地 Git 验证。测试仓库当前没有 PR，未做远端真实 PR 全链路发布。 |
| 上下文管理 | `agent/core/repo_memory.py`：提交级结构、测试入口、约定片段；blob 级复用、原子缓存、脏目录失效；仅引用成功任务的历史摘要，保留 run/message/事件来源 | 单元测试验证代码提交后未变 README 片段复用，读取数由 1 变为 0；失败/运行中任务不进入历史检索，仓库之间不混用摘要。大仓库时间/Token 收益尚未压测。 |
| 评测与观测 | 8 个固定样例、隐藏断言、结构化审查、可复现裁定、同集基线对照；API→执行进程 W3C Trace，阶段 JSONL span、持久化事件、Token 聚合与页面展示 | 9 月 25 日固定集 8/8；4 条输出缺陷逐条容器复现。9 月 26 日 48 项后端、7 项前端测试通过，前端类型与生产构建通过。Collector、告警、长周期事件归档未部署。 |

## 修复流程如何使用

1. 在首页选择 GitHub 仓库，展开“审查 GitHub Pull Request”，输入已存在的 PR 编号。
2. 审查完成后，任务页显示文件、变更行、严重度、问题描述。勾选要修复的问题，输入容器内验收命令，例如 `python -m pytest /projects/仓库名/tests -q`。
3. 点击确认后创建修复任务。任务启动前验证 worktree 的 head/diff 摘要；快照变化则失败并要求重新审查，不能在未确认的新代码上直接修复。
4. 修复后执行给定命令；非零退出码会使任务失败，文件和日志保留。验收通过后独立 Reviewer 复审，剩余 findings 可再次确认。修复不会自动 push 或发布 PR。

PR 是固定的历史提交快照；远端后续提交不会自动合并进当前审查。需要更新范围时新建审查。私有仓库和 GitHub API 限流场景需在本地 `.env` 配置 `GITHUB_TOKEN`，不要把凭据填进任务文本。

## 实际验收记录

- 工程回归：`test-results/evaluation.json`，48 通过、0 失败、0 跳过，包含真实 PostgreSQL/Redis/Docker。测试服务地址明确使用 `127.0.0.1`；本机 `localhost` 并发连接曾挂起，单项诊断和完整重跑均通过。
- 前端：TypeScript 通过，3 个测试文件、7 个测试通过；9 月 26 日通过现有依赖入口 `node node_modules/vite/bin/vite.js build` 完成生产构建。现有依赖缺少 `.bin` 命令链接，类型检查和测试对应使用 `node node_modules/typescript/bin/tsc --noEmit`、`node node_modules/vitest/vitest.mjs run`。
- 本轮审查报告验收：`test-results/review-flow-1790386906/report.json`。真实模型先输出 `changes_requested`，修复验收退出码 0，独立复审输出 `no_findings`；报告绑定新快照。修复阶段 8 次调用、输入 50,873 / 输出 879 Tokens。
- 真实修复流程：`test-results/review-flow-1790308248/report.json`。模型识别 `take` 的边界条件回归，确认后修复，容器断言通过，独立复审剩余 findings 为 0。修复 run 共 8 次模型调用、输入 50,822 / 输出 1,118 Tokens，耗时 31.701 秒。这是单个小样例结果。
- 最新跨进程部署任务：`test-results/deployment-smoke.json`，run `2dd1a32a-7260-48c6-993b-d5583cb11dc9` completed，49 条事件、5 次模型调用，输入 46,204 / 输出 538 Tokens；请求 Trace ID 为 `29f8139a67eed48f20a006a26b785e43`。

## 模型评测与收益口径

所有完整评测使用同一份 `evals/tasks.json`，报告记录 suite SHA256；下面均为单次运行结果，模型波动、请求延迟和调试选择影响比较，不能把差值当作已证明的因果收益。

| 配置/阶段 | 报告目录（位于 test-results） | 通过样例 | 备注 |
| --- | --- | --- | --- |
| 完整上下文、文本 JSON | `model-eval-1790213656` | 7/8 | 无缺陷回答附带多余文本，JSON 解析失败 |
| 按需检索、文本 JSON | `model-eval-1790308268` | 6/8 | 分块实现未通过断言；无缺陷回答格式失败 |
| 初次结构化交付 | `model-eval-1790308399` | 7/8 | 多缺陷样例未产生 structured_response |
| 增加最终格式处理 | `model-eval-1790308633` | 7/8 | 空文本不能恢复实际的非法工具参数 |
| 增加只读重试并保留协议诊断 | `model-eval-1790308843` | 7/8 | 捕获 malformed tool JSON；缺失对应 tool 回执导致下一请求 400 |
| 修复工具回执协议后的验收 | `model-eval-1790309036` | 8/8 | 4 个审查样例均交付，TP=4、FP=0、FN=0；单样例诊断结果没有拼进本次完整集 |

最后一轮平均每例 5.611 秒，总输入 80,589 / 输出 2,349 Tokens。完整上下文基线输入为 62,931 Tokens，因此本组小文件数据**不能支持“按需检索必然降低 Token”**。目前可确证的改进是：非法工具调用的协议恢复通过可重复的工程测试验证，最终种子集验收通过；检索策略对真实大仓库的收益需要另建代表性样例并多次测量。

有效缺陷率与定位精确率分开。最终 4 条 finding 的描述已经逐条检查，并在容器内复现边界拒绝、默认列表共享、首元素漏加、奇偶判断错误；裁定绑定完整 finding 的 SHA256，见 `judgments.json` 和 `adjudicated-report.json`。本组输出有效缺陷率为 4/4，裁定者为助手源码检查加执行复现，不是外部专家盲测。无缺陷对照返回空列表，审查输出覆盖率为 4/4。

## 故障定位与运行管理

任务页“运行证据”展示阶段、耗时和 Trace ID；`GET /api/runs/{id}/metrics` 聚合模型调用、Tokens 和阶段事件。模型未返回 usage 时会统计缺失次数，而不是声称准确计费。`GET /events?after=<cursor>` 和 SSE `Last-Event-ID` 可以回放。

OpenTelemetry 默认写 `logs/traces-<pid>.jsonl`，包含父子 span 关系；可用 `CODEPILOT_OTEL_CONSOLE=true` 查看控制台 span。HTTP 只记录方法、状态码，不导出提示词和凭据。

当前控制台：<http://127.0.0.1:3000>；API：<http://127.0.0.1:8000/docs>。主机 Worker 日志为 `logs/compose-worker-v4.stdout.log` / `logs/compose-worker-v4.stderr.log`，PID 在 `logs/compose-worker.pid`。基础服务用 `docker compose --profile web stop` 停止。重新启动 Worker 使用 `.\.venv\Scripts\python.exe scripts/compose_worker.py`。单操作者模式不应直接暴露公网。

## 可据实使用的简历内容

**CodePilot：面向研发协作的可恢复 AI Coding Agent 平台**  
技术栈：Python、FastAPI、DeepAgents/LangGraph、PostgreSQL、Redis Streams、Docker、Git、SSE、OpenTelemetry。

面向需求实现与 PR 审查，构建覆盖仓库理解、方案确认、代码修改、容器验收和审查反馈的 AI Coding 平台，通过持久化执行、受限运行环境和固定评测提升长任务的可控性。

- **任务可靠性：** 分离 API 与 Agent Worker，以数据库任务状态、Redis Streams 消费组、租约令牌和 Checkpoint 管理执行生命周期，支持幂等提交、取消、硬超时、有界显式恢复及事件回放；通过外部操作台账与结果不确定阻断控制重复发布。
- **执行隔离：** 基于 Git worktree 隔离会话代码目录，在非 root、禁网及资源受限容器中执行生成代码；凭据留在可信 Git/SCM 控制器，对 push、PR 发布设置绑定具体提交与参数摘要的审批。
- **审查协作：** 固定 GitHub PR base/head 快照，通过只读 Reviewer 输出可定位 findings，结合 diff 行号校验、去重、人工确认和修复前快照检查，串联代码修复、容器验收与独立复审。
- **上下文管理：** 按仓库和提交维护结构、测试入口及技术约定，按 Git blob 复用未变化文件片段，对脏工作区失效；结合带任务、消息来源的历史摘要与按需检索构建上下文。
- **评测观测：** 建立缺陷修复、功能实现和审查固定集，区分成功率、定位精确率/召回率及经复现裁定的有效缺陷率，记录耗时和 Token；通过跨进程 Trace、阶段事件与回放定位故障，并修复非法工具 JSON 引发的协议重试失败。

建议简历以工程能力为主，面试时展示上述报告和复现过程；不要写未测量的生产吞吐、团队提效或通用成功率。

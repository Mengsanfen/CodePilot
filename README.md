# CodePilot

面向研发协作的可恢复 AI Coding Agent：通过持久化调度、独立 Worker、受限执行和人工审批，让长任务的失败可定位、恢复有边界、结果可验证。

技术栈：Python 3.14、FastAPI、DeepAgents 0.6.11 / LangGraph、SQLite / PostgreSQL、Redis Streams、Docker、React、SSE、OpenTelemetry。

学习与面试准备：从 [CodePilot 学习与面试教程](docs/LEARNING_GUIDE.md) 开始，按源码链路、动手实验和追问逐步掌握项目。

## 为什么需要 Harness

代码生成只是任务的一环。Worker 可能重启，模型可能反复检索，测试可能失败，远端 PR 可能已创建但响应丢失。CodePilot 把任务事实、执行权限、发布审批与验证证据放到模型之外管理，让这些失败有明确的后续处理方式。

## 架构

```mermaid
flowchart TD
    UI[React UI / API Client] --> API[FastAPI]
    API --> DB[(PostgreSQL / SQLite\nRun · Lease · Operation · Event)]
    API -. 唤醒 .-> Redis[Redis Streams]
    Redis -. 通知 .-> Worker[Host Worker Supervisor]
    DB -->|领取 / 心跳 / fencing| Worker
    Worker --> Runner[Agent Runner / LangGraph Checkpoint]
    Runner --> Files[Session Git Worktree / 文件工具]
    Runner --> Sandbox[Docker Sandbox\n禁网 · 非 root · 资源限额]
    Runner -->|只提案| Approval[不可变参数审批]
    UI -->|人工确认| Approval
    Approval --> SCM[Trusted Git / SCM Controller]
    SCM --> Remote[GitHub / Gitee]
    DB -->|持久化 SSE 回放| UI
    Runner --> Trace[OpenTelemetry / Usage / 阶段事件]
```

只有可信控制器持有 Git/SCM 凭据；任务容器没有 Docker socket 或发布凭据。Redis 是唤醒优化，数据库是任务事实源。分层职责和限制见 [SECURITY_MODEL](docs/SECURITY_MODEL.md)。

## 能做什么

- HTTP 提交立即返回 `202 + run_id`，Worker 独立执行；支持幂等提交、取消、硬超时、租约失效检测及显式 Checkpoint 恢复。
- 每个会话独立 Git worktree；同一会话同一时刻只运行一个任务。后续对话保留当前修改。
- 先方案、再确认实施。Reviewer 使用单独编译的只读 Agent，文件写入和 shell 在后端被拒绝。
- 推送、PR、评论先生成不可变申请，展示完整参数后由用户审核；网络结果不确定时不自动重复发布。
- 按提交版本缓存仓库结构、测试入口和来源片段，脏工作区跳过缓存。
- SSE 使用持久化序号及 `Last-Event-ID` 回放；页面显示真实执行状态、恢复和发布审批。
- 按 GitHub PR 编号固定提交快照；人工选择 findings 后，执行修复、容器验收和独立复审。页面可查看阶段追踪与模型用量。

## 失败后如何处理

| 场景 | 系统行为 |
| --- | --- |
| Worker 消失、租约过期 | 标记 interrupted；旧 lease token 无法写事件或完成任务 |
| 显式恢复 | 人工核对外部副作用后恢复 Checkpoint；已完成图节点不应重跑，未完成节点仍可能重入 |
| Redis 通知丢失 | Worker 轮询数据库继续领取任务 |
| PR 请求响应超时 | 操作进入 uncertain，拒绝自动重复发布 |
| SSE 连接断开 | 客户端按最后事件序号重新订阅持久化事件 |

Checkpoint 不能保证外部副作用 Exactly Once，恢复也不等于无条件重试。状态转换、恢复前提与人工核对步骤见 [FAILURE_SEMANTICS](docs/FAILURE_SEMANTICS.md)。

## Windows 本地运行

运行前需要 Git、Python 3.14 和 uv；前端需要 Node.js 24 与 Yarn 1.22.22。容器执行还需要 Docker Desktop 的 Linux containers。

```powershell
uv venv --python 3.14
uv pip install -c constraints.txt -e ".[test]"
# 仅首次配置时复制，不覆盖已有 .env
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn agent.app:app --host 127.0.0.1 --port 8000
```

在 `.env` 配置 `DEEPSEEK_API_KEY`。模型名称以账户 `/models` 返回为准；本次验证使用 `deepseek-v4-pro`。密钥不应写入源码、命令参数或评测报告。

默认 SQLite + 内嵌 Worker，适合可信本地开发。`CODEPILOT_EXECUTION_BACKEND=local` 会在当前用户权限下执行命令，路径检查和命令黑名单**不构成系统沙箱**。运行生成代码时建议使用下面的 Docker Worker。

前端：

```powershell
cd ui
yarn install --frozen-lockfile
# 前端 .env.local：VITE_DASHBOARD_API_BASE_URL=http://127.0.0.1:8000
yarn dev
```

## Docker Compose 部署

默认采用 **API、PostgreSQL、Redis 在 Compose 中运行，Worker 在 Windows 主机运行并通过 Docker CLI 创建任务容器** 的方式。Compose 不挂载 Docker socket。

1. 启动 Docker Desktop 的 Linux containers。
2. 在项目 `.env` 设置 `CODEPILOT_HOST_WORKSPACE` 为一个已创建的绝对目录，使用正斜杠，例如 `C:/codepilot-workspace`。API 和 Worker 必须指向同一目录。
3. 设置 `POSTGRES_PASSWORD`，首次启动后不要仅修改环境变量期望数据库密码自动改变。
4. 执行：

```powershell
docker build -f Dockerfile.sandbox -t codepilot-sandbox:local .
docker compose up -d --build
.\.venv\Scripts\python.exe scripts/compose_worker.py
```

多个主机 Worker 进程可共享同一 PostgreSQL、Redis 和工作区。数据库负责领取任务，Redis 仅用于唤醒，因此丢失通知不会丢失任务。

可选静态前端：先在 `ui` 执行 `yarn build`，构建时不设置外部 API base（使用同源请求），然后运行 `docker compose --profile web up -d web`，访问 `http://127.0.0.1:3000`。Nginx 已关闭 SSE 缓冲。

任务容器使用非 root 用户、只读根文件系统、无网络、CPU/内存/PID 限额，只挂载当前会话项目。所需语言和依赖应预装到 `Dockerfile.sandbox` 或自定义 `CODEPILOT_SANDBOX_IMAGE`，不在无网络容器内临时安装。Linux 主机需确保挂载项目的 UID/GID 写入权限；本次目标环境为 Windows Docker Desktop。

平台目前是**单操作者开发平台**，没有多租户身份与仓库 ACL，不应直接暴露公网。Compose 端口只绑定回环地址。持有主机 Docker CLI 权限的 Worker 属于可信控制器，生成代码的容器拿不到 Docker socket、模型密钥或数据库凭据。

## API

交互文档：`http://127.0.0.1:8000/docs`。

| 动作 | 接口 |
| --- | --- |
| 提交任务 | `POST /api/tasks`，可带 `Idempotency-Key` |
| 查询执行 | `GET /api/runs/{run_id}` |
| 取消 | `POST /api/runs/{run_id}/cancel` |
| 恢复 | `POST /api/runs/{run_id}/resume`，body `{"external_effects_checked":true}` |
| 回放事件 | `GET /api/runs/{run_id}/events?after=0` |
| SSE | `GET /api/runs/{run_id}/stream`，支持 `Last-Event-ID` |
| 查看发布申请 | `GET /api/runs/{run_id}/operations` |
| 批准操作 | `POST /api/runs/{run_id}/operations/{operation_id}/approve`，body `{"expected_digest":"<operation_id>"}` |
| 审查 PR | `POST /api/reviews`，body `{"repo_url":"https://github.com/owner/repo","pr_number":1}` |
| 审查发现 | `GET /api/runs/{run_id}/findings` |
| 确认修复 | `POST /api/runs/{run_id}/repairs`，body 包含 `finding_ids`、`expected_digest`、`verification_command` |
| 运行指标 | `GET /api/runs/{run_id}/metrics` |

GitHub/Gitee HTTPS URL 均可用于任务。GitHub 发布需要本地配置 `GITHUB_TOKEN`；没有 Token 仍可分析公开仓库。Git 推送只允许固定 commit 到 `codepilot/` 分支，不允许强制推送。

## 验证与评测

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py

# 真实基础设施集成测试（独立测试 schema / Redis key，不删除应用数据）
$env:TEST_POSTGRES_URL='postgresql://codepilot:你的密码@127.0.0.1:54329/codepilot'
$env:TEST_REDIS_URL='redis://127.0.0.1:63799/0'
$env:TEST_DOCKER_IMAGE='codepilot-sandbox:local'
.\.venv\Scripts\python.exe scripts/evaluate.py

# 以下会调用真实模型并产生 API 费用
.\.venv\Scripts\python.exe scripts/smoke_agent.py
.\.venv\Scripts\python.exe scripts/eval_tasks.py --live
.\.venv\Scripts\python.exe scripts/smoke_review_flow.py --live

# 20 个固定公开仓库任务：先验证夹具，再测真实模型
docker build -f deploy/Dockerfile.benchmark -t codepilot-benchmark:local .
.\.venv\Scripts\python.exe scripts/benchmark.py --validate
.\.venv\Scripts\python.exe scripts/benchmark.py --live
```

当前主评测集覆盖 Click、ItsDangerous、HTTPX 三个固定提交快照上的缺陷修复、功能扩展、测试生成、审查与仓库问答，共 20 例。方法、预算和局限见 [BENCHMARK](docs/BENCHMARK.md)。它是人工编写任务集，不代表历史 Issue 或企业任务分布。

报告写入忽略目录 `test-results/`。工程回归与模型能力评测分开：`evals/tasks.json` 包含 8 个固定合成样例（缺陷修复 2、功能实现 2、审查 4，含无缺陷对照）；隐藏断言在模型完成后才注入，代码在受限容器内运行。报告区分任务成功率、定位精确率/召回率和审查交付覆盖率。有效缺陷率必须通过逐条证据裁定，使用 `scripts/adjudicate_eval.py 报告路径 裁定JSON路径`，不能只按行号匹配生成。它们不能代表真实仓库总体成功率。

对照运行支持 `--context-mode full|on-demand`、`--review-output json|structured`，传 `--baseline <report.json>` 计算同评测集的差异；不同评测集或不完整样例集拒绝比较。`--case-id <id>` 仅用于诊断。保留失败记录，不用单次成功重跑替换失败报告。

API 与 Agent 执行进程通过持久化 W3C traceparent 关联，阶段 span 默认写入 `logs/traces-<pid>.jsonl`，运行事件记录阶段耗时、失败类型和模型 Token。设置 `CODEPILOT_OTEL_CONSOLE=true` 可同时输出控制台 span。当前没有接入外部 Collector 或实现完整成本结算。

更多设计、限制和面试讲解见 [架构说明](docs/CODEPILOT_ARCHITECTURE.md)，最新目标对标与实际执行结果见 [目标验收](docs/TARGET_ALIGNMENT.md)，首轮部署记录见 [本地验收记录](docs/VALIDATION.md)。

## 演示入口

- 正常任务：在 UI 提交公开仓库任务，先查看方案，再确认实施；观察事件、测试结果和 Git diff。
- 恢复：对具有执行上下文的 interrupted 任务，核对 Git/PR 结果后通过恢复按钮继续；查看同一 run 的 attempt 和事件序号。
- 发布审批：检查申请中的仓库、分支、固定 commit 和内容后批准；uncertain 状态需先核对远端。
- 审查闭环：提交 GitHub PR，选择具体 findings 并指定验收命令，查看修复、容器验证和独立复审阶段。可用 `scripts/smoke_review_flow.py --live` 验证固定测试链路。

## 范围与限制

项目面向单操作者的 Agent Runtime 工程验证。没有租户权限系统、自动扩缩容或生产 SLO；Docker 约束不代表强对抗多租户隔离。固定评测数量有限，单次通过率不用于推断生产可靠性。远端 CI 状态以 GitHub 实际运行结果为准，本地回归通过不等同于 CI Green。

## Credits / Upstream

项目来源与保留模块见 [UPSTREAM](docs/UPSTREAM.md)，框架能力与项目改造边界见 [OWNERSHIP](docs/OWNERSHIP.md)。

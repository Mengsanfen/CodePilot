# CodePilot 本地验收记录

验收日期：2026-09-24。环境：Windows、Python 3.14.2、Docker Desktop Linux containers。以下记录来自实际执行，不代表生产规模或通用 Agent 成功率。

## 已验证结果

| 验证范围 | 结果与证据 |
| --- | --- |
| 后端工程回归 | 29 项通过，0 失败、0 跳过；包括真实 PostgreSQL、Redis、Docker，以及 Checkpoint 恢复、并发领取、租约失效、取消和进程树清理。原始报告：`test-results/evaluation.json`。 |
| 前端 | TypeScript 检查通过，2 个测试文件中的 5 项测试通过，生产构建通过。发布审批测试确认必须勾选审核，并发送不可变操作摘要。 |
| 真实模型种子评测 | DeepSeek `deepseek-v4-pro` 完成缺陷修复、功能实现、审查定位三个样例；分别耗时 11.79、9.45、3.99 秒。报告：`test-results/model-eval-1790175053/report.json`。执行镜像为当时已缓存的 `python:3.12-slim`；正式 Python 3.14 沙箱镜像另通过隔离集成测试。 |
| 部署链路 | Nginx → Linux API → PostgreSQL / Redis → Windows Worker → DeepSeek，读取 `Mengsanfen/CodePilot` 的 README。任务完成，attempt=1，37 条持久化事件。报告：`test-results/deployment-smoke.json`。 |
| 部署页面 | 首页返回 200；默认仓库读取 `DEFAULT_REPO_URL`；任务详情显示持久化执行结果。已修复 SPA 首页缺少默认 index 导致的 403。 |

任务 ID：`227541aa-5c7a-4756-b7cd-591297885471`；会话 ID：`83919739-6985-478c-a277-d5b954a0333b`。测试报告目录被 Git 忽略；如需分享，先检查报告中仓库路径和模型输出。

## 使用当前部署

- 控制台：<http://127.0.0.1:3000>。
- API 文档：<http://127.0.0.1:8000/docs>。
- API、Nginx、PostgreSQL、Redis 由本项目 Compose 管理；Worker 在 Windows 主机运行，通过 Docker CLI 创建受限任务容器。
- 后台 Worker 日志位于 `logs/compose-worker.stdout.log`、`logs/compose-worker.stderr.log`，当前进程号记录在 `logs/compose-worker.pid`。关闭此 Worker 前应核对进程仍属于本项目；重新启动可在项目根目录执行 `.\.venv\Scripts\python.exe scripts/compose_worker.py`，该前台进程使用 Ctrl+C 停止。
- 停止基础服务使用 `docker compose --profile web stop`；再次启动使用 `docker compose --profile web up -d`。不要用删除数据卷代替停止服务。

## 验收边界

三个模型样例仅验证评测流程及样例结果，没有大规模真实 Issue 基准、吞吐/延迟压测或生产收益数据。发布审批和远端分支 SHA 校验通过模拟 GitHub 响应验证，本次未实际 push、发布 PR 或评论。

平台面向可信单操作者，尚无多租户身份认证、仓库 ACL、跨主机存储编排或完善的运维告警。OpenTelemetry 已有运行 span，但没有完成 Collector 部署与全链路传播。详细恢复语义、执行边界及可据实使用的简历表述见 [架构说明](CODEPILOT_ARCHITECTURE.md)。

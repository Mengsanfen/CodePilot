# LX-AICODING Course Backend

Windows 本地运行版 AI Coding 教学项目。

课程方案已安装到：

```text
docs\LX_AICODING_COURSE_REBUILD_PLAN.md
```

## 启动

项目配置文件为：

```text
.env
```

如果本项目 `.env` 中填写了同名变量，本项目配置优先。

```powershell
scripts\start_backend.cmd
```

启动前端：

```powershell
scripts\start_ui.cmd
```

同时启动前后端：

```powershell
.venv\Scripts\python.exe scripts\start_all.py
```

打开：

```text
http://127.0.0.1:2024/health
http://127.0.0.1:3000/agents
```

后端通过 FastAPI/Uvicorn 运行，不使用 `langgraph dev`。

## 创建任务

```powershell
$body = @{
  repo_url = "https://gitee.com/owner/repo.git"
  prompt = "创建一个最小 FastAPI 项目，添加 /health 和 pytest 测试，并创建 PR"
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:2024/api/tasks -Body $body -ContentType "application/json"
```

## 数据文件

```text
data\checkpoints.sqlite  LangGraph checkpoint，保存聊天历史和 thread state
data\store.sqlite        业务 Store，保存任务摘要、PR URL、review findings
```

## 日志文件

```text
logs\backend-YYYY-MM-DD.log      FastAPI、接口、配置和异常日志
logs\agent-runs-YYYY-MM-DD.log   Agent 任务运行日志，包括 clone、测试、push、PR 等步骤
```

浏览器查看最近日志：

```text
http://127.0.0.1:2024/api/logs/backend
http://127.0.0.1:2024/api/logs/agent
```

查看指定日期的历史日志：

```text
http://127.0.0.1:2024/api/logs/backend?date=2026-06-22
http://127.0.0.1:2024/api/logs/agent?date=2026-06-22
```

## 验证

基础后端验证，不会调用模型或 push：

```powershell
.venv\Scripts\python.exe scripts\verify_backend.py
```

真实 Gitee 端到端验证会调用模型并创建 PR，请只对测试仓库运行：

```powershell
.venv\Scripts\python.exe scripts\verify_gitee_e2e.py https://gitee.com/owner/repo.git
```

当前真实验收仓库：

```text
https://gitee.com/msb-goldbin/ai_coding
```

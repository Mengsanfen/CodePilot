# 固定仓库评测

`evals/repository-tasks.json` 定义 20 个任务：缺陷修复 6、功能扩展 4、测试生成 3、只读审查 4、仓库问答 3。来源为 Click 8.1.8、ItsDangerous 2.2.0、HTTPX 0.28.1，具体 Git SHA 固定在 manifest。代码从上游公开仓库获取到忽略目录，保留快照中的许可文件，不将三份源码复制进平台仓库。

这些是公开仓库快照上的人工编写任务和注入回归，不是历史 Issue 修复集，也不代表企业任务分布。ItsDangerous 规模较小，不能把三个仓库都称为中大型项目。旧 `evals/tasks.json` 八例作为轻量 smoke 保留。

评测入口使用项目模型配置、文件后端、中间件及 Docker 验收，运行一个受限的文件工具 Agent。它不经过 API 排队、完整方案审批或线上 Reviewer 图，因此这里的 task_success_rate 是该评测执行链路的任务通过率，不能称为完整平台端到端成功率。API/Worker 恢复与发布审批由工程回归和独立故障注入验证。

## 重现

```powershell
docker build -f Dockerfile.sandbox -t codepilot-sandbox:local .
docker build -f deploy/Dockerfile.benchmark -t codepilot-benchmark:local .
.\.venv\Scripts\python.exe scripts/benchmark.py --validate
# 使用本地 .env 中的 DeepSeek 配置，以下产生模型调用费用
.\.venv\Scripts\python.exe scripts/benchmark.py --live
```

首次运行需要联网下载固定快照及镜像依赖；任务验收在禁网、非 root、资源受限容器中执行。模型进程只有文件工具，execute 从工具列表移除，后端也拒绝执行；生成代码不会在宿主机运行。模型结束后控制器才注入隐藏断言。`--case-id` 只做诊断，不替代完整报告。

## 验收方法

- Bugfix：先确认注入缺陷不能通过隐藏断言，再确认参考修复通过；断言涵盖边界和公开调用路径。
- Feature：原版缺少能力应失败，参考扩展应通过。
- Testgen：模型只能新增 bench_tests 测试；原始源码上必须通过，预设回归变体上必须产生断言失败。收集错误不算检出缺陷，不允许通过修改源码逃避验证。
- Review：含两个注入缺陷和两个干净对照。缺陷位置由固定补丁与可执行正负对照确认，输出按路径/行号定位计分。
- Repo QA：检查必需概念与有效源码行范围。这是有界定位评分，不是完整的自然语言语义正确性评判。

夹具验证与模型评测分开报告；`--validate` 的通过率不能写成 Agent 成功率。试跑失败及最终固定集失败都保留。

## 指标和限制

报告包含任务成功率、编码成功率、首次任务尝试成功率、延迟 median/p95（nearest rank）、输入/输出 Token、逻辑模型调用次数、工具调用次数、实际读取文件数与失败类别。每个任务上限为 20 次逻辑模型调用和 240 秒，超时终止模型子进程，验收命令使用剩余预算；容器清理有额外宽限时间。HTTP SDK 的内部网络重试不是新的 Agent 模型调用，不能用该计数推算完整计费请求数。

Usage 回调在每次调用后持久化，即使后续任务失败也保留已知用量。若请求中断前未收到 usage，报告标记 usage_complete=false；已知 Token 是下界，不能把缺失值解释为免费调用。原始文本/错误仅存忽略目录，源码摘要和公开报告不包含凭据。

Review 分别报告定位 precision/recall、干净样例的误报比例和结构化交付率。没交付的正例仍计入漏检分母。有效 finding 率需要逐条证据裁定，尚未裁定时为 null，不能拿定位匹配率替代。可靠性指标来自独立故障实验，不从编码通过率推算。

数据集指纹覆盖 manifest 与隐藏断言，报告另外记录评测器源码摘要。预算、数据集、模型、镜像或评分发生变化时须公开说明，禁止只替换失败样例。CI 运行离线工程测试；真实模型评测必须显式 `--live`。

完整结果可用 `python scripts/summarize_benchmark.py test-results/benchmark-<运行编号>/report.json` 导出到 `docs/benchmark/latest-summary.md`。导出器拒绝夹具报告和不完整任务集，只公开白名单指标，不复制 prompt、finding 文本或原始错误。需要有效缺陷率时，逐条阅读输出并填写与 finding 摘要绑定的裁定，再运行 `scripts/adjudicate_eval.py`；它生成新文件，保留原始报告。

预算校准：两次 qa-click 试跑使用 8 次调用上限，均在检索过程中触及上限；正式 20 任务报告前统一调整为 20 次、240 秒。试跑与正式数据集指纹不同，不计算二者的收益差异。

首次全量运行发现评测器的图步数上限 50 会在约 10 次调用时触发 GraphRecursionError。这是评测基础设施的预算实现错误，不应解释成模型推理失败。该次运行已中止，原始部分报告保留为诊断，不计入最终分母。修复后增加使用相同中间件和文件工具的离线图测试：第 20 次可正常回答，第 21 次调用必须被 ModelCallLimit 拒绝；重新运行全部 20 例，不能只重跑受影响项。

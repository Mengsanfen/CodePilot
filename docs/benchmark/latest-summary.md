# 固定仓库评测结果

运行日期：2026-10-11；模型：`deepseek-v4-pro`；每例预算 20 次逻辑模型调用、240 秒。原始报告编号：`benchmark-1791703712750028300`。

本页由 `scripts/summarize_benchmark.py` 从完整模型报告生成。原始报告仅存 `test-results/`，不提交模型输出、仓库源码或环境配置。

人工编写任务集，含注入回归；每例单次任务尝试，任务内允许多轮工具调用。成功率不代表企业或历史 Issue 的总体效果。方法见 [BENCHMARK](../BENCHMARK.md)。

| 指标 | 实测 |
| --- | --- |
| cases | 20 |
| passed | 12 |
| task_success_rate | 0.6 |
| coding_success_rate | 0.8462 |
| first_attempt_success_rate | 0.6 |
| median_latency_seconds | 27.9365 |
| p95_latency_seconds | 71.98 |
| input_tokens | 994756 |
| output_tokens | 35361 |
| model_calls | 177 |
| tool_calls | 176 |
| files_read | 50 |
| usage_complete | True |
| review_location_precision | None |
| review_location_recall | 0.0 |
| review_clean_case_false_positive_rate | None |
| review_delivery_rate | 0.0 |
| review_valid_finding_rate | None |
| reliability | None |

`review_valid_finding_rate` 未裁定时为 None；定位匹配不等于缺陷语义正确。可靠性指标由独立故障实验报告。

## 分类结果

| 类型 | 通过 / 总数 |
| --- | --- |
| bugfix | 6 / 6 |
| feature | 3 / 4 |
| testgen | 2 / 3 |
| review | 0 / 4 |
| repoqa | 1 / 3 |

## 全部任务

| 任务 | 结果 | 失败类型 | 秒 | 模型调用 | 输入 / 输出 Token |
| --- | --- | --- | --- | --- | --- |
| fix-click-bool | 通过 | — | 19.394 | 3 | 9155 / 296 |
| fix-click-normalize | 通过 | — | 16.841 | 3 | 8809 / 316 |
| fix-its-age-boundary | 通过 | — | 18.517 | 4 | 15207 / 419 |
| fix-its-base64 | 通过 | — | 24.145 | 6 | 19526 / 606 |
| fix-httpx-query-copy | 通过 | — | 19.485 | 3 | 9297 / 265 |
| fix-httpx-query-add | 通过 | — | 26.04 | 6 | 27366 / 793 |
| feature-click-slug | 通过 | — | 29.443 | 8 | 29688 / 840 |
| feature-click-positive | 失败 | TIMEOUT | 52.933 | 20 | 115231 / 2336 |
| feature-its-batch | 通过 | — | 40.341 | 13 | 64899 / 1285 |
| feature-httpx-nonempty | 通过 | — | 44.763 | 12 | 62505 / 1588 |
| review-its-age-boundary | 失败 | MODEL_REASONING | 43.209 | 6 | 32623 / 2498 |
| review-httpx-query-copy | 失败 | MODEL_REASONING | 25.081 | 5 | 21704 / 753 |
| review-clean-click | 失败 | MODEL_REASONING | 87.461 | 5 | 29816 / 8484 |
| review-clean-httpx | 失败 | MODEL_REASONING | 26.43 | 3 | 10341 / 1131 |
| testgen-click-bool | 失败 | TIMEOUT | 52.05 | 20 | 119815 / 2752 |
| testgen-its-age-boundary | 通过 | — | 54.881 | 16 | 120721 / 2761 |
| testgen-httpx-query-add | 通过 | — | 71.98 | 19 | 188606 / 4783 |
| qa-click | 失败 | MODEL_REASONING | 40.312 | 13 | 63400 / 1718 |
| qa-itsdangerous | 失败 | CONTEXT_MISSING | 26.039 | 7 | 28233 / 898 |
| qa-httpx | 通过 | — | 26.233 | 5 | 17814 / 839 |

## 可追溯版本

- suite_sha256: `eaa6860089d35664539ae650b006d7081863ee399b971ad92687f5c36aa2bd4d`
- verifier_sha256: `7418e4db1dbaccb77eed06ece54116a71ba9fd29d7e102c6ba36c3976e2692da`
- image_id: `c84b9c901b2ecb160abcdad27b15921f145251dee562d7290e4326464029c89e`

本轮在 Windows + Docker Desktop 上执行，使用本地配置的 DeepSeek 模型。原始逐例记录包含模型标识和 usage 完整性；缺失 usage 时已知 Token 只能作为下界。

## 失败分析与解释边界

- 2 例 TIMEOUT 实际是耗尽 20 次逻辑调用，并非均耗时 240 秒：Click 正整数功能扩展和布尔测试生成。模型在实现、检索、编辑周边文件之间消耗预算。
- 5 例 MODEL_REASONING 是严格 JSON 交付失败：4 个 review 和 qa-click。模型在 JSON 前输出解释文字，解析器拒绝整段响应。两个注入缺陷的原始文本识别到了问题，但未交付有效结构化结果，因此不改为通过。
- qa-itsdangerous 的 CONTEXT_MISSING 是定位规则未满足：输出给出了实际比较分支的行范围，规则要求覆盖另一固定锚点。这暴露了当前 QA 规则的假阴性风险，不能据此断言模型不理解过期语义。保留原始评分，后续修改 rubric 时必须更换版本，不计算跨规则收益。
- review 结构化交付为 0/4，因此 precision、有效 finding 率及干净对照误报率不可估计，不能写成“零误报”；两个未交付缺陷仍计入 recall 分母。
- 本次是开发机单轮观测，部分夹具验证与其重叠运行。延迟是本轮耗时，不是受控吞吐实验或服务 SLO。

工程验收独立通过：20/20 夹具正负对照，62/62 后端回归（包括 PostgreSQL、Redis、Docker）。它们不计入以上模型成功率。

# 升级执行记录

依据用户提供的 `CODEPILOT_INTERNSHIP_UPGRADE_SPEC.md`，按 P0–P5 分阶段实施。
开始时仓库只有 `init project` 提交，并有此前升级与用户保留的未提交改动。本轮保留这些工作；P0 提交包含已有升级基线和本轮交付修复，不将全部基线代码归为本轮新增。

## P0 验收

- 修复 `AgentRunLimitTracker` 文档字符串缩进，编译通过。
- 新增仅报告路径和规则名的源码 Secret Scan；本地 `.env` 保留，未跟踪、不打包。
- 新增源码包导出，取消跟踪 IDE 配置（本地文件保留），统一 Yarn 1.22.22 和锁文件，移除重复 Bun 锁文件。
- CI 增加前端、源码扫描、编译及手动 Docker 检查；CI 不调用真实模型。
- 49 项后端测试通过，包含 PostgreSQL、Redis、Docker；实际报告见忽略目录 `test-results/p0-integration.log`。
- 独立 Python 环境安装成功，Windows 与 Linux 干净源码测试均为 46 passed / 3 skipped（未配置外部服务）。日志：`p0-fresh-install.log`、`p0-fresh-tests.log`、`p0-linux.log`。
- 2026-10-07 最终后端验证：49 passed / 0 failed / 0 skipped，50.70 秒；`p0-final-backend.log`。
- Vite 7/8 双版本触发 Yarn 1 的链接错误；统一兼容的 Vite 7.3.3，并固定 Nitro 版本。空目录离线冻结安装成功（`p0-frontend-fresh.log`），typecheck 成功，7 项前端测试通过，生产构建成功（111.97 秒）。保留上游 chunk 大小及 SSR 指令警告，不作为运行失败。
- 编译无错误、Secret Scan 0 项；源码包排除本地环境与运行资产，符号链接不能导出工作区外文件。
- 修正 Windows 启动脚本的硬编码绝对路径及旧端口；评测报告使用本次独立 XML，避免崩溃后误读旧结果。

远端 GitHub Actions 状态需要对应提交在 GitHub 运行后确认；本地检查通过不称为远端 CI Green。

## 交付约定

原始日志、模型输出、工作区和数据库只放忽略目录。公开证据只记录汇总、固定版本、方法和限制。
后续各阶段的变更及验证结果追加在此文件。

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

## P1 验收（2026-10-07）

- UI 包名、浏览器存储键及运行环境变量统一 CodePilot；旧配置别名集中在 env_utils，增加新配置优先级测试。
- 历史教学和简历材料移至 archive/teaching；新增 UPSTREAM 与 OWNERSHIP，保留使用中的上游组件来源注释。
- 删除 13 个无后端能力的路由/组件，以及导入图与引用搜索确认的 24 个孤立模块。配置页改为只读真实配置；审查页复用已实现的 PR 审查入口。移除无效登录/退出和配置保存操作。
- 删除已移除组件的 TypeScript 排除列表；仍使用的上游组件部分保留 ts-nocheck，未声称全量严格类型覆盖。
- 编译成功；后端 50 passed，前端 7 passed，typecheck 通过，构建成功（58.14 秒）；日志 p1-backend / p1-frontend / p1-typecheck / p1-build。
- Breaking change：删除无效旧页面；浏览器旧主题/侧栏/草稿键不迁移。数据库与服务端历史任务保留。

## P2 验收（2026-10-10）

- 新增 Typed Operation，约束 git_push/create_pr/comment_pr 与 proposed/approved/executing/completed/uncertain；兼容旧 pending/post_comment 的原始指纹。
- 执行前重算持久化参数指纹，审批与执行分别提交事务；行锁领取防止并发重复执行。补充篡改、并发、旧记录与 approved 状态恢复测试。
- Gitee 发布前补充远端分支 SHA 校验；GitHub 原有固定 head 校验保留。未知操作类型不能进入可信发布控制器。
- 新增 SECURITY_MODEL、FAILURE_SEMANTICS，说明可信边界、逻辑分层、恢复条件及 Checkpoint 非 Exactly Once。
- 后端完整集成回归 57 passed / 0 failed / 0 skipped（99.57 秒）；前端 9 passed，typecheck 与 build 成功；编译成功，Secret Scan 0 项。日志 p2-final-backend / p2-frontend-final / p2-typecheck / p2-build。
- API 新提案状态改为 proposed，新增 expected_digest；前端兼容旧状态。旧审批记录无需破坏性迁移。远端 CI 仍待实际提交运行。

## P3 验收（2026-10-11）

- 新增 20 个固定 Git SHA 的公开仓库任务、严格任务 Schema、隐藏正负验收、测试生成变异验证及 12 类失败分类。保留旧 8 例 smoke。
- 模型仅使用文件工具，生成代码由可信验收器在受限 Docker 中执行；原始输出留在忽略目录。新增公开汇总导出器，拒绝夹具或不完整任务集。
- 修复回调并发写文件问题，以及图步数上限提前切断调用预算的问题；新增真实图循环测试验证第 20 次允许、第 21 次拒绝。
- 20/20 夹具验证通过（p3-controls-final.log）；62 项后端测试通过，0 failed / 0 skipped，32.71 秒（p3-final-backend.log）；编译和 Secret Scan 通过。
- 完整模型报告 benchmark-1791703712750028300：12/20 通过，编码类 11/13；994,756 输入 Token、35,361 输出 Token、177 次调用，usage 完整。median 27.937 秒，p95 71.98 秒。
- 8 个失败全部保留：预算耗尽 2、JSON 交付失败 5、QA 锚点规则未满足 1。后者存在规则假阴性，审查交付 0/4 不能解释为零误报。详见 benchmark/latest-summary.md。
- 两次 8-call 试跑及图步数错误的中止报告仅作诊断，不混入完整报告。评测器为受限文件工具链路，不宣称经过完整 API/审批/线上 Reviewer。
- README 增加架构、失败语义、评测与演示入口。P3 不修改现有业务 API 或数据库结构；前端沿用 P2 已通过的检查。

## 主分支合并前验证（2026-10-12）

- 在 Windows 本地启动独立的临时 PostgreSQL 17、Redis 7.4 容器，使用现有 `codepilot-sandbox:local` 镜像；验证结束后清理本次创建的容器。
- `scripts/failure_injection.py`：5 passed / 0 failed / 0 skipped，pytest 耗时 41.02 秒。覆盖 SQLite/PostgreSQL 的真实 Worker 中断与 Checkpoint 恢复、模拟 Redis 通知丢失、模拟 PR 已接受但响应超时，以及真实 Docker 权限与隔离负向检查。
- 本次两个恢复用例均通过，旧租约写入被拒绝，事件重放一致；模拟发布场景中重复外部操作计数为 0。仅为单次固定故障实验结果，不代表生产 SLO、统计可靠性或跨平台验收。
- 其余后端回归：62 passed / 0 failed / 0 skipped，32.62 秒；两组共 67 项全部通过。原始结果位于忽略目录 `test-results/faults-1791815536538722900/report.json` 与 `test-results/main-update-regression.xml`。
- Secret Scan：0 项。保留 Starlette TestClient/httpx 弃用警告；本轮未重新运行真实模型评测，前端沿用 P2 已通过的检查。
- 同步学习教程中的本地故障实验验收状态；P5 ContextStrategy 对照实验仍未完成，远端 CI 以实际运行结果为准。

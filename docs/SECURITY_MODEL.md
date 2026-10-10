# 执行边界

## 可信控制器

FastAPI、Worker Supervisor、RunStore、Approval Executor、SCM Controller 与主机 Docker CLI 属于可信域。它们持有平台运行和远端发布需要的权限，部署账户与主机安全是前提。API 当前面向单操作者、回环地址部署，没有多租户身份认证或仓库 ACL。

LLM 输出、生成代码、仓库内容、工具参数和下载内容均按不可信输入处理。仓库说明与历史摘要提供证据，不能自行授予发布权限。模型工具只能提出结构化发布申请，不能直接调用审批端点或把可信 SCM 控制器当任意 shell。

## 代码执行

Docker backend 创建会话命令容器，固定 `--user 65534:65534`、`--network none`、`--read-only`、`--cap-drop ALL`、`--security-opt no-new-privileges`，并限制 CPU、Memory、PID。临时目录使用 tmpfs，仅挂载当前会话 projects 目录。模型/Git/数据库凭据不通过容器环境传入，不挂载宿主机 `.env`、其他会话或 Docker socket。

API/PostgreSQL/Redis 可在 Compose 内运行；Worker 在主机通过 Docker CLI 创建容器。Linux 挂载目录必须配置对应非 root UID 的写权限。依赖在沙箱镜像中预装；禁网环境不会临时联网安装。

Docker 降低执行风险，不宣称强对抗多租户隔离。镜像供应链、内核漏洞、主机 Docker 权限、CPU/IO 耗尽与控制器自身漏洞仍需部署层治理。Local backend 在当前账户权限下执行，路径约束和命令名单不是 OS 沙箱。

## 发布与审批

Typed Operation 只接受 `git_push`、`create_pr`、`comment_pr`。审批绑定 run、kind 与完整参数的 SHA-256 指纹；提案持久化后由 UI 展示，用户提交 expected_digest。执行前在数据库事务内重新计算记录指纹，拒绝参数变化和未知类型；并发请求只有一个能把 approved 记录领取为 executing。

Git push 固定 commit，限制 codepilot/ 分支、不强推。GitHub 创建 PR 校验远端 head 与提案中的 commit 一致，再创建 draft 或复用已有 PR；Gitee 通过可信 Git 控制器读取远端分支 SHA 后校验。远端校验与发布不是原子操作，其他操作者并发改动远端分支仍需核对。数据库指纹防止意外参数变更，不是抵抗已控制数据库和 API 主机的攻击者的数字签名。

外部请求发送后超时属于 uncertain，禁止自动重发。执行进程崩溃后残留 executing 同样视为“需核对”，不会被后台重试。审批入口与状态机见 [FAILURE_SEMANTICS](FAILURE_SEMANTICS.md)。

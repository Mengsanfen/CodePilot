# 会话工作区

每个会话使用独立 Git worktree，相同会话的后续任务保留未提交修改。
`/projects/<repo>` 是当前会话仓库；`/policies/repository-context.json` 描述基准版本和可回溯来源。
提交发生变化时上下文缓存失效；脏工作区应重新读取必要文件。
`/skills`、`/policies`、`/runtimes`、`/logs` 只读，`.secrets`、`.git` 与环境配置不可读取。
命令执行是一次性的，目录切换不会延续到下个工具调用。使用明确的 `/projects/<repo>` 路径。

# 分享可复现的源码

在项目根目录运行 `python scripts/check_secrets.py`，再运行 `python scripts/package_review.py`。
产物是 `dist/codepilot-review.zip`，打包前对实际入包文件进行相同扫描。扫描输出只包含路径和规则名。

压缩包排除 `.git`、虚拟环境、缓存、依赖、构建资源、工作区、数据库、日志、测试报告、IDE 配置及本地 `.env`。
保留 `.env.example` 和没有凭据的前端构建配置。本地真实 `.env` 不删除、不打包。
扫描覆盖 Key、Token、Bearer 和数据库 URL；明确的示例占位符与测试密码允许使用，不能将真实密码加入白名单。
此扫描是提交防线，不保证发现任意格式的秘密。分享前仍应确认未添加私人仓库源码或个人数据。

在空目录解压后按 README 安装依赖。不要直接压缩整个工作目录，也不要执行 `git add -f .env`。

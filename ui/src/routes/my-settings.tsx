import { Navigate, createFileRoute } from "@tanstack/react-router"
import { AppShell, SettingsSection } from "@/components/AppShell"
import { useProfile } from "@/lib/profile"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/my-settings")({ component: RuntimeSettings })

function RuntimeSettings() {
  const session = useSession()
  const profile = useProfile()
  if (session.isLoading) return <p role="status" className="p-6">正在读取运行配置…</p>
  if (!session.data) return <Navigate to="/login" />
  const rows = [
    ["模型", profile.data?.default_model],
    ["默认仓库", profile.data?.default_repo],
    ["基准分支", profile.data?.base_branch],
    ["发布分支前缀", profile.data?.branch_prefix],
  ]
  return <AppShell user={session.data} title="运行配置" description="CodePilot · 单操作者研发工作台">
    <SettingsSection title="当前服务配置" description="配置来自服务端环境变量。修改后重启 API 与 Worker 生效。">
      {profile.isLoading && <p role="status" className="p-4 text-sm">正在加载…</p>}
      {profile.error && <p role="alert" className="p-4 text-sm text-red-600">读取失败：{profile.error.message}</p>}
      {profile.data && <dl className="divide-y divide-border">{rows.map(([label, value]) =>
        <div key={label} className="grid gap-2 px-4 py-4 sm:grid-cols-[8rem_1fr]">
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="break-all font-mono text-sm">{value || "未配置"}</dd>
        </div>)}
      </dl>}
    </SettingsSection>
    <p className="text-sm leading-6 text-muted-foreground">在任务页提交需求或查看执行事件；在 PR 审查页发起只读审查。推送、PR 发布和评论需要在对应任务中确认完整操作参数。</p>
  </AppShell>
}

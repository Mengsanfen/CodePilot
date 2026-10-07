import { Navigate, createFileRoute } from "@tanstack/react-router"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/login")({ component: ConnectionStatus })

function ConnectionStatus() {
  const session = useSession()
  if (session.data) return <Navigate to="/agents" />
  return <main className="flex min-h-svh items-center justify-center p-6">
    <div className="max-w-md space-y-4 rounded-lg border border-border p-8">
      <h1 className="font-heading text-xl">CodePilot</h1>
      <p role={session.error ? "alert" : "status"} className="text-sm text-muted-foreground">{session.isLoading ? "正在连接本地服务…" : "无法连接后端。请确认 API 已启动及前端 API 地址配置正确。"}</p>
      {session.error && <button onClick={() => void session.refetch()} className="rounded border border-border px-3 py-2 text-sm">重新连接</button>}
    </div>
  </main>
}

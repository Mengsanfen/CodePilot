import { useState } from "react"
import { Navigate, createFileRoute } from "@tanstack/react-router"
import { AppShell } from "@/components/AppShell"
import { NewReview } from "@/components/agents/NewReview"
import { useSession } from "@/lib/session"
import { useProfile } from "@/lib/profile"

export const Route = createFileRoute("/review")({ component: ReviewPage })

function ReviewPage() {
  const session = useSession()
  const profile = useProfile()
  const [repo, setRepo] = useState<string | undefined>()
  const value = repo ?? profile.data?.default_repo ?? ""
  if (session.isLoading) return <p role="status" className="p-6">正在加载…</p>
  if (!session.data) return <Navigate to="/login" />
  return <AppShell user={session.data} title="PR 审查" description="固定 base/head 提交，生成可定位的审查发现，再由你选择是否修复。">
    <div className="space-y-3">
      <label htmlFor="review-repo" className="block text-sm font-medium">GitHub 仓库地址</label>
      <input id="review-repo" type="url" value={value} onChange={event => setRepo(event.target.value)} placeholder="https://github.com/owner/repo" className="w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-sm" />
    </div>
    <NewReview repo={value.trim() || null} />
  </AppShell>
}

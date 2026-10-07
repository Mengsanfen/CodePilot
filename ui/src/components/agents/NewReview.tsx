import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { agentThreadKeys } from "@/lib/agents/queries"

export function NewReview({ repo }: { repo: string | null }) {
  const [number, setNumber] = useState("")
  const navigate = useNavigate()
  const client = useQueryClient()
  const mutation = useMutation({ mutationFn: async () => {
    const base = (import.meta.env.VITE_DASHBOARD_API_BASE_URL ?? "").replace(/\/$/, "")
    const result = await fetch(`${base}/api/reviews`, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ repo_url: repo, pr_number: Number(number) }) })
    const data = await result.json()
    if (!result.ok) throw new Error(data.detail || "提交审查失败")
    return data as { thread_id: string }
  }, onSuccess: data => { void client.invalidateQueries({ queryKey: agentThreadKeys.all }); void navigate({ to: "/agents/$threadId", params: { threadId: data.thread_id } }) } })
  return <details className="w-full rounded-lg border border-current/10 p-4 text-sm"><summary className="cursor-pointer font-medium">审查 GitHub Pull Request</summary>
    <p className="my-3 text-xs opacity-60">读取所选仓库的 PR，固定提交版本进行只读审查；发现的问题需确认后才进入修复。</p>
    <div className="flex gap-2"><input aria-label="PR 编号" className="min-w-0 flex-1 rounded border border-current/20 bg-transparent p-2" type="number" min="1" placeholder="PR 编号" value={number} onChange={e => setNumber(e.target.value)} />
      <button className="rounded border px-3 disabled:opacity-40" disabled={!repo || !Number.isInteger(Number(number)) || Number(number) < 1 || mutation.isPending} onClick={() => mutation.mutate()}>开始只读审查</button></div>
    {mutation.error && <p role="alert" className="mt-2 text-red-600">{mutation.error.message}</p>}
  </details>
}

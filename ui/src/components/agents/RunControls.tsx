import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { AgentThread } from "@/lib/agents/types"
import { agentThreadKeys } from "@/lib/agents/queries"
import { runRequest, type Operation } from "@/lib/agents/runs"
import { ReviewPanel } from "./ReviewPanel"

const labels: Record<string, string> = {
  queued: "排队中", running: "执行中", cancelling: "正在停止", cancelled: "已取消",
  interrupted: "执行中断", completed: "已完成", failed: "执行失败",
}
const operations: Record<string, string> = { git_push: "推送代码", create_pr: "创建 PR", comment_pr: "发布评论", post_comment: "发布评论" }

export function RunControls({ thread }: { thread: AgentThread }) {
  const run = thread.execution
  const client = useQueryClient()
  const [reviewed, setReviewed] = useState<Record<string, boolean>>({})
  const list = useQuery({
    queryKey: ["run-operations", run?.run_id],
    queryFn: () => runRequest<Array<Operation>>(run!.run_id, "/operations"),
    enabled: !!run,
    refetchInterval: run?.status === "running" ? 3000 : false,
  })
  const mutation = useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) => runRequest(run!.run_id, path, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: agentThreadKeys.detail(thread.id) })
      void client.invalidateQueries({ queryKey: ["run-operations", run?.run_id] })
    },
  })
  if (!run) return null
  const pending = list.data?.filter(item => item.status !== "completed") ?? []
  return (
    <section aria-label="任务运行与发布审核" className="mx-auto w-full max-w-3xl border-b border-current/10 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="font-semibold">{labels[run.status] ?? run.status}</span>
        <span className="text-xs opacity-60">第 {run.attempt} 次执行</span>
        <code title={run.run_id} className="ml-auto text-xs opacity-50">{run.run_id.slice(0, 8)}</code>
      </div>
      {run.error && <p role="status" className="mt-2 text-red-600 dark:text-red-400">{run.error}</p>}
      {run.status === "interrupted" && (
        <div className="mt-3 space-y-2">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!reviewed[run.run_id]} onChange={e => setReviewed({ ...reviewed, [run.run_id]: e.target.checked })} />
            已核对远端提交和发布结果，确认可以从保存的进度恢复
          </label>
          <button className="rounded border px-3 py-1.5 disabled:opacity-40" disabled={!reviewed[run.run_id] || mutation.isPending}
            onClick={() => mutation.mutate({ path: "/resume", body: { external_effects_checked: true } })}>恢复任务</button>
        </div>
      )}
      {pending.map(item => (
        <details key={item.operation_id} className="mt-3 border-l-2 border-amber-500 pl-3">
          <summary className="cursor-pointer font-medium">{operations[item.kind] ?? item.kind} · {["proposed", "pending"].includes(item.status) ? "待审核" : item.status === "approved" ? "已批准，待执行" : "需核对远端结果"}</summary>
          <pre className="my-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-black/5 p-3 text-xs dark:bg-white/5">{JSON.stringify(item.parameters, null, 2)}</pre>
          {["proposed", "pending", "approved"].includes(item.status) && <>
            <label className="flex items-center gap-2"><input type="checkbox" checked={!!reviewed[item.operation_id]}
              onChange={e => setReviewed({ ...reviewed, [item.operation_id]: e.target.checked })} />已核对以上仓库、内容和提交</label>
            <button className="mt-2 rounded border px-3 py-1.5 disabled:opacity-40"
              disabled={!reviewed[item.operation_id] || mutation.isPending || run.status !== "completed"}
              onClick={() => mutation.mutate({ path: `/operations/${item.operation_id}/approve`, body: { expected_digest: item.expected_digest ?? item.operation_id } })}>
              批准并{operations[item.kind] ?? "执行"}
            </button>
          </>}
        </details>
      ))}
      {(mutation.error || list.error) && <p role="alert" className="mt-2 text-red-600">{(mutation.error || list.error)?.message}</p>}
      <ReviewPanel key={run.run_id} runId={run.run_id} threadId={thread.id} status={run.status} />
    </section>
  )
}

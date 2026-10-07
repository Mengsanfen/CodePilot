import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { runRequest } from "@/lib/agents/runs"
import { agentThreadKeys } from "@/lib/agents/queries"

type Finding = { finding_id: string; digest: string; decision: string | null; fix_run_id: string | null;
  payload: { file: string; line: number; title: string; severity: string; description: string } }
type Metrics = { model_calls: number; input_tokens: number; output_tokens: number; missing_usage_calls: number;
  stages: Array<{ event: string; stage: string; trace_id: string; seconds?: number }> }
type ReviewReport = { report: null | { outcome: "incomplete" | "changes_requested" | "no_findings"; head: string; digest: string;
  finding_ids: string[]; reasons: string[]; excluded_files: Array<{ file: string; reason: string }>;
  verification: null | { exit_code: number; command: string; workspace_changed: boolean }; recorded_at: string } }
const outcomes = { incomplete: "审查不完整", changes_requested: "发现待处理问题", no_findings: "本次审查未发现缺陷" }
const reasons: Record<string, string> = { missing_or_stale_diff_read: "缺少当前快照的 diff 阅读证据", diff_truncated: "diff 超出阅读上限",
  files_excluded: "部分文件未纳入审查", missing_review_summary: "缺少审查总结", verification_failed_or_stale: "验收失败或代码在验收后发生变化" }

export function ReviewPanel({ runId, threadId, status }: { runId: string; threadId: string; status: string }) {
  const client = useQueryClient()
  const [selected, setSelected] = useState<string[]>([])
  const [command, setCommand] = useState("")
  const polling = status === "running" ? 3000 : false
  const list = useQuery({ queryKey: ["findings", runId, status], queryFn: () => runRequest<Finding[]>(runId, "/findings"), refetchInterval: polling })
  const metrics = useQuery({ queryKey: ["metrics", runId, status], queryFn: () => runRequest<Metrics>(runId, "/metrics"), refetchInterval: polling })
  const report = useQuery({ queryKey: ["review-report", runId, status], queryFn: () => runRequest<ReviewReport>(runId, "/review-report"), refetchInterval: polling })
  const chosen = list.data?.filter(item => selected.includes(item.finding_id)) ?? []
  const repair = useMutation({
    mutationFn: () => runRequest(runId, "/repairs", { finding_ids: selected, expected_digest: chosen[0]?.digest, verification_command: command }),
    onSuccess: () => { setSelected([]); void client.invalidateQueries({ queryKey: agentThreadKeys.detail(threadId) }); void client.invalidateQueries({ queryKey: ["findings", runId] }) },
  })
  return <div className="mt-3 space-y-3">
    {report.data?.report && <section aria-label="审查结论" className="rounded border border-current/15 p-3 text-sm">
      <strong>{outcomes[report.data.report.outcome]}</strong>
      <p className="mt-1 text-xs opacity-70">结论仅适用于所记录的代码快照，任务执行完成不代表代码没有缺陷。</p>
      <p className="mt-1 break-all font-mono text-xs">HEAD {report.data.report.head.slice(0, 12)} · 快照 {report.data.report.digest.slice(0, 12)}</p>
      {report.data.report.reasons.map(reason => <p key={reason} className="mt-1 text-xs">{reasons[reason] ?? reason}</p>)}
      {!!report.data.report.excluded_files.length && <details className="mt-1 text-xs"><summary>未审查文件 · {report.data.report.excluded_files.length}</summary>
        {report.data.report.excluded_files.map(item => <p key={item.file}>{item.file} · {item.reason}</p>)}
      </details>}
      {report.data.report.verification && <p className="mt-2 text-xs">验收命令退出码：{report.data.report.verification.exit_code}
        {report.data.report.verification.workspace_changed && " · 命令修改了工作区，需重新验收"}</p>}
    </section>}
    {metrics.data && <details className="text-xs opacity-80"><summary className="cursor-pointer">运行证据 · {metrics.data.model_calls} 次模型调用 · 输入 {metrics.data.input_tokens} / 输出 {metrics.data.output_tokens} Tokens</summary>
      {metrics.data.missing_usage_calls > 0 && <p>有 {metrics.data.missing_usage_calls} 次调用未返回 Token 用量。</p>}
      {metrics.data.stages?.map((item, index) => <p key={index} className="mt-1 break-all">{item.stage} · {item.event.replace("stage.", "")} {item.seconds === undefined ? "" : `${item.seconds}s`} · trace {item.trace_id}</p>)}
    </details>}
    {!!list.data?.length && <details open><summary className="font-medium">审查发现 · {list.data.length}</summary>
      <p className="my-2 text-xs opacity-60">选择确认要修复的问题，并填写容器内验收命令。修复完成后执行测试与独立复审。</p>
      {list.data.map(item => <label key={item.finding_id} className="my-2 flex items-start gap-2 rounded border border-current/10 p-3">
        <input type="checkbox" aria-label={`确认修复 ${item.payload.title}`} checked={selected.includes(item.finding_id)}
          disabled={!!item.decision || status !== "completed" || repair.isPending}
          onChange={e => setSelected(e.target.checked ? [...selected, item.finding_id] : selected.filter(id => id !== item.finding_id))} />
        <span><strong>{item.payload.title}</strong> · {item.payload.severity}<br /><code>{item.payload.file}:{item.payload.line}</code><br />{item.payload.description}
          {item.decision && <span className="block text-xs">已确认 · 修复任务 {item.fix_run_id?.slice(0, 8)}</span>}</span>
      </label>)}
      <label className="block text-xs">容器内验收命令<input aria-label="容器内验收命令" className="mt-1 w-full rounded border border-current/20 bg-transparent p-2 font-mono" value={command} onChange={e => setCommand(e.target.value)} placeholder="python -m pytest /projects/仓库名/tests -q" /></label>
      <button className="mt-2 rounded border px-3 py-1.5 disabled:opacity-40" disabled={!chosen.length || !command.trim() || new Set(chosen.map(f => f.digest)).size !== 1 || repair.isPending || status !== "completed"}
        onClick={() => repair.mutate()}>确认所选问题并修复</button>
    </details>}
    {(repair.error || list.error || metrics.error || report.error) && <p role="alert" className="text-red-600">{(repair.error || list.error || metrics.error || report.error)?.message}</p>}
  </div>
}

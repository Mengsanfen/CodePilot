const base = (import.meta.env.VITE_DASHBOARD_API_BASE_URL ?? "").replace(/\/$/, "")

export interface Operation {
  operation_id: string
  kind: string
  status: string
  parameters: Record<string, unknown>
}

export function runUrl(runId: string, suffix = "") {
  return `${base}/api/runs/${encodeURIComponent(runId)}${suffix}`
}

export async function runRequest<T>(runId: string, suffix: string, body?: unknown): Promise<T> {
  const response = await fetch(runUrl(runId, suffix), {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error.detail || `请求失败 (${response.status})`)
  }
  return response.json() as Promise<T>
}

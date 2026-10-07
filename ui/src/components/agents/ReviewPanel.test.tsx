// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReviewPanel } from "./ReviewPanel"

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it("requires selecting a finding and providing verification before creating a bound repair", async () => {
  const finding = { finding_id: "f1", digest: "sha256-diff", decision: null, payload: { file: "app.py", line: 2, title: "边界错误", severity: "high", description: "合法边界输入抛异常" } }
  const fetcher = vi.fn(async (url: unknown, options?: RequestInit) => ({ ok: true, json: async () =>
    options?.method === "POST" ? {} : String(url).endsWith("/findings") ? [finding] : { stages: [], model_calls: 3, input_tokens: 100, output_tokens: 20 } }))
  vi.stubGlobal("fetch", fetcher)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ReviewPanel runId="r1" threadId="t1" status="completed" /></QueryClientProvider>)
  const checkbox = await screen.findByRole("checkbox", { name: "确认修复 边界错误" })
  const button = screen.getByRole("button", { name: "确认所选问题并修复" }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  fireEvent.click(checkbox)
  expect(button.disabled).toBe(true)
  fireEvent.change(screen.getByRole("textbox", { name: "容器内验收命令" }), { target: { value: "python -m pytest /projects/test/tests -q" } })
  fireEvent.click(button)
  await waitFor(() => expect(fetcher.mock.calls.some(([url, options]) => String(url).endsWith("/repairs") && options?.body === JSON.stringify({ finding_ids: ["f1"], expected_digest: "sha256-diff", verification_command: "python -m pytest /projects/test/tests -q" }))).toBe(true))
})

it("shows incomplete review evidence even when execution completed", async () => {
  const report = { outcome: "incomplete", head: "a".repeat(40), digest: "b".repeat(64), finding_ids: [],
    reasons: ["diff_truncated", "files_excluded"], excluded_files: [{ file: ".env", reason: "sensitive" }],
    verification: { exit_code: 0, command: "pytest", workspace_changed: true }, recorded_at: "2026-09-26" }
  vi.stubGlobal("fetch", vi.fn(async (url: unknown) => ({ ok: true, json: async () =>
    String(url).endsWith("/review-report") ? { report } : String(url).endsWith("/findings") ? [] : { stages: [] } })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ReviewPanel runId="r1" threadId="t1" status="completed" /></QueryClientProvider>)
  expect(await screen.findByText("审查不完整")).toBeTruthy()
  expect(screen.getByText("diff 超出阅读上限")).toBeTruthy()
  expect(screen.getByText(/命令修改了工作区/)).toBeTruthy()
  expect(screen.queryByText("本次审查未发现缺陷")).toBeNull()
})

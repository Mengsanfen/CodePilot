// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RunControls } from "./RunControls"
import type { AgentThread } from "@/lib/agents/types"

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it.each(["pending", "proposed", "approved"])("requires explicit review for %s before sending publication approval", async (status) => {
  const operation = { operation_id: "exact-digest", expected_digest: "exact-digest", kind: "git_push", status, parameters: { branch: "codepilot/test", commit: "abc" } }
  const fetcher = vi.fn(async (_url: unknown, options?: RequestInit) => ({
    ok: true, json: async () => options?.method === "POST" ? {} : String(_url).endsWith('/operations') ? [operation] : String(_url).endsWith('/findings') ? [] : {stages: []},
  }))
  vi.stubGlobal("fetch", fetcher)
  const thread = { id: "thread", execution: { run_id: "run", status: "completed", attempt: 1 } } as AgentThread
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><RunControls thread={thread} /></QueryClientProvider>)
  const summary = await screen.findByText(status === "approved" ? "推送代码 · 已批准，待执行" : "推送代码 · 待审核")
  fireEvent.click(summary)
  const button = screen.getByRole("button", { name: "批准并推送代码" }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  expect(fetcher.mock.calls.every(([, opts]) => opts?.method !== "POST")).toBe(true)
  fireEvent.click(screen.getByRole("checkbox"))
  fireEvent.click(button)
  await waitFor(() => expect(fetcher.mock.calls.some(([url, opts]) =>
    String(url).endsWith("/operations/exact-digest/approve") && opts?.body === '{"expected_digest":"exact-digest"}')).toBe(true))
})

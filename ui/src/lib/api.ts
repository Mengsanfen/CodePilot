/** Typed client for the CodePilot dashboard API. UI origins are configured by deployment. */

const API_BASE = (import.meta.env.VITE_DASHBOARD_API_BASE_URL ?? "").replace(/\/$/, "");

if (!API_BASE && typeof window !== "undefined") {
  console.warn("VITE_DASHBOARD_API_BASE_URL is not set");
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export function isGithubReauthError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.status === 401) return true;
  return /github token|gitee token|source-control token|re-login required/i.test(error.message);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}/dashboard/api${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface SessionUser {
  login: string;
  email: string | null;
  avatar_url: string | null;
  is_admin: boolean;
  slack_oauth_enabled?: boolean;
}

export interface ModelOption {
  id: string;
  label: string;
  efforts: Array<string>;
  default_effort: string;
  supports_images: boolean;
}

export interface OptionsPayload {
  models: Array<ModelOption>;
  default_agent_model: string;
  default_agent_reasoning_effort: string;
  default_agent_subagent_model: string;
  default_agent_subagent_reasoning_effort: string;
}

export interface Profile {
  login?: string;
  email?: string;
  default_model?: string;
  reasoning_effort?: string;
  default_subagent_model?: string | null;
  subagent_reasoning_effort?: string | null;
  default_repo?: string | null;
  base_branch?: string | null;
  branch_prefix?: string | null;
  auto_fix_ci?: boolean;
  create_prs?: boolean;
  review_draft_prs?: boolean | null;
  updated_at?: string;
}

export interface ProfileUpdate {
  default_model: string;
  reasoning_effort: string;
  default_subagent_model?: string | null;
  subagent_reasoning_effort?: string | null;
  default_repo?: string | null;
  base_branch?: string | null;
  branch_prefix?: string | null;
  auto_fix_ci?: boolean;
  create_prs?: boolean;
  review_draft_prs?: boolean | null;
}

export type TriggerMode = "every_push" | "once_per_pr" | "manual";
export type AutofixMode = "off" | "low" | "medium" | "high";

export interface TeamSettings {
  trigger_mode: TriggerMode;
  review_draft_prs: boolean;
  pr_summaries: boolean;
  review_trace_links: boolean;
  autofix_mode: AutofixMode;
  autofix_severity_threshold: AutofixMode;
  org_guidelines?: string | null;
  default_agent_model?: string | null;
  default_agent_reasoning_effort?: string | null;
  default_agent_subagent_model?: string | null;
  default_agent_subagent_reasoning_effort?: string | null;
  default_repo?: string | null;
  default_reviewer_model?: string | null;
  default_reviewer_reasoning_effort?: string | null;
  default_reviewer_subagent_model?: string | null;
  default_reviewer_subagent_reasoning_effort?: string | null;
  updated_at?: string | null;
}

export interface UserMapping {
  github_login: string;
  work_email: string;
  slack_user_id?: string | null;
  source?: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
}

export interface UserMappingsPage {
  items: Array<UserMapping>;
  total: number;
  page: number;
  page_size: number;
}

export type UsageLeaderboardPeriod = "7d" | "30d" | "all";

export interface UsageLeaderboardRow {
  rank: number;
  user: {
    name: string;
    github_login: string | null;
    email: string | null;
  };
  favorite_model: string;
  agent_runs: number;
  prs_opened: number;
  merged_prs: number;
  agent_loc: number;
  additions: number;
  deletions: number;
}

export interface ReviewerStatsCounterRow {
  name: string;
  count: number;
}

export interface ReviewerStatsPayload {
  period: UsageLeaderboardPeriod;
  reviewed_prs: number;
  prs_with_findings: number;
  findings_recorded: number;
  surfaced_findings: number;
  addressed_findings: number;
  resolved_after_update: number;
  dismissed_findings: number;
  unresolved_surfaced_findings: number;
  resolution_rate: number;
  human_replies: number;
  severity_counts: Record<string, number>;
  top_categories: Array<ReviewerStatsCounterRow>;
  generated_at_ms: number | null;
}

export interface UsageLeaderboardPayload {
  period: UsageLeaderboardPeriod;
  rows: Array<UsageLeaderboardRow>;
  total_members: number;
  current_user_rank: number | null;
  generated_at_ms: number | null;
  reviewer_stats: ReviewerStatsPayload;
}

export interface Repository {
  full_name: string;
  private: boolean;
}

export interface Installation {
  id: number;
  account: string | null;
  account_type: string | null;
}

export interface ReposPayload {
  installations: Array<Installation>;
  repositories: Array<Repository>;
}

export type ReviewStyleStatus = "idle" | "running" | "completed" | "failed";

export interface ReviewStyle {
  full_name: string;
  owner?: string;
  name?: string;
  status: ReviewStyleStatus;
  custom_prompt: string | null;
  analysis_summary: string | null;
  top_reviewers: Array<string>;
  prs_sampled: number;
  reviews_sampled: number;
  analysis_thread_id: string | null;
  analysis_run_id: string | null;
  error: string | null;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
}

export const api = {
  me: () => request<SessionUser>("/me"),
  options: () => request<OptionsPayload>("/options"),
  profile: () => request<Profile>("/profile"),
  repos: () => request<ReposPayload>("/repos"),
};

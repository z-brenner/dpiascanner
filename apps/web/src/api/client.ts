import type {
  Config,
  Finding,
  FindingDetail,
  FindingsDiff,
  Installation,
  Page,
  Repo,
  ReportJson,
  Resolution,
  Run,
  RunOptions,
  RunSummary,
  Settings,
  User,
} from "./types";

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  config: () => request<Config>("/config"),
  me: () => request<User>("/auth/me"),
  logout: () => post<{ ok: boolean }>("/auth/logout", {}),
  callback: (code: string, installationId: string | null, setupAction: string | null) =>
    post<{ user: User; installations: Installation[] }>("/auth/github/callback", {
      code,
      installation_id: installationId ? Number(installationId) : null,
      setup_action: setupAction,
    }),
  installations: () =>
    request<{ installations: Installation[]; install_url: string }>("/installations"),
  repos: (q: string, page = 1) =>
    request<Page<Repo>>(`/repos?${new URLSearchParams({ q, page: String(page), per_page: "30" })}`),
  resolve: (input: string) => post<Resolution>("/repos/resolve", { input }),
  createRun: (owner: string, repo: string, ref: string | null, options: RunOptions) =>
    post<RunSummary>("/runs", { owner, repo, ref, options }),
  run: (id: string) => request<Run>(`/runs/${encodeURIComponent(id)}`),
  cancel: (id: string) => post<{ id: string; status: string }>(`/runs/${encodeURIComponent(id)}/cancel`, {}),
  findings: (id: string) =>
    request<Page<Finding>>(`/runs/${encodeURIComponent(id)}/findings?per_page=200`),
  finding: (id: string, findingId: string) =>
    request<FindingDetail>(`/runs/${encodeURIComponent(id)}/findings/${encodeURIComponent(findingId)}`),
  report: (id: string) => request<ReportJson>(`/runs/${encodeURIComponent(id)}/report?format=json`),
  reportUrl: (id: string, format: "md" | "html" | "docx" | "json") =>
    `${API_BASE}/runs/${encodeURIComponent(id)}/report?format=${format}&download=true`,
  repoRuns: (owner: string, repo: string) =>
    request<Page<RunSummary>>(
      `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/runs?per_page=50`,
    ),
  diff: (a: string, b: string) =>
    request<FindingsDiff>(`/runs/${encodeURIComponent(a)}/diff/${encodeURIComponent(b)}`),
  settings: () => request<Settings>("/settings"),
};

export type Api = typeof api;

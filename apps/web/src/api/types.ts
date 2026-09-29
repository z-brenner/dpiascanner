export type Severity = "critical" | "high" | "medium" | "low" | "informational";
export type RunStatus = "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled";
export type StageName = "clone" | "parse" | "graph" | "registry" | "classify" | "verify" | "report";

export interface Config {
  install_url: string;
  app_slug: string;
  /** "stub" classifies on the server; "jev" sends redacted summaries to TypeSafe's Jev. */
  decision_provider: string;
  session_ttl_hours: number;
}

export interface User {
  login: string;
  name: string | null;
  avatar_url: string | null;
}

export interface Installation {
  id: number;
  account: string;
  account_type: string;
  repository_selection: string;
  suspended: boolean;
}

export interface Repo {
  id: number;
  full_name: string;
  private: boolean;
  default_branch: string;
  installation_id: number;
}

export interface Page<T> {
  total: number;
  page: number;
  per_page: number;
  items: T[];
}

export interface Resolution {
  input: string;
  status: "installed" | "public" | "inaccessible" | "invalid";
  scannable: boolean;
  full_name: string | null;
  owner: string | null;
  repo: string | null;
  installation_id: number | null;
  private: boolean | null;
  default_branch: string | null;
  reason: string | null;
  install_url: string | null;
}

export interface RunOptions {
  languages: ("python" | "typescript")[] | null;
  dynamic: boolean;
  dependency_depth: number;
}

export interface Stage {
  stage: StageName;
  status: "pending" | "running" | "done" | "failed" | "skipped" | string;
  started_at: string | null;
  finished_at: string | null;
  info: Record<string, unknown>;
}

export interface RunSummary {
  id: string;
  repo: string;
  ref: string;
  sha: string;
  status: RunStatus;
  trigger: string;
  mode: string;
  pr_number: number | null;
  current_stage: StageName | null;
  created_at: string | null;
  finished_at: string | null;
  findings: number | null;
  unresolved: number | null;
}

export interface Run extends RunSummary {
  options: Record<string, unknown>;
  stages: Stage[];
  coverage: Record<string, number>;
  summary: Record<string, unknown>;
  error: { stage: string | null; message: string | null } | null;
}

export interface Finding {
  id: string;
  key: string;
  category: string;
  title: string;
  severity: Severity;
  severity_modifiers: string[];
  status: "resolved" | "unresolved";
  verification: "verified" | "inferred" | "observed-unexpected" | "not-run" | string;
  data_categories: string[];
  destination_class: string | null;
  statute_refs: string[];
  reachable: boolean;
  node_ids: string[];
  edge_ids: string[];
  decision_ids: string[];
  evidence: Record<string, unknown>;
}

export interface FindingNode {
  id: string;
  kind: "source" | "transform" | "sink" | "retention";
  symbol: string;
  file: string;
  line_start: number;
  line_end: number;
  snippet: string;
  url: string | null;
}

export interface FindingStep {
  file: string;
  line: number;
  kind: string;
  note?: string;
  unresolved?: boolean;
  function?: string;
  url: string | null;
}

export interface FindingDecision {
  id: string;
  target_id: string;
  question: string;
  answer: string;
  probability: number;
  distribution: Record<string, number>;
  provider: string;
}

export interface FindingDetail extends Finding {
  nodes: FindingNode[];
  edges: { id: string; from: string; to: string; kind: string; steps: FindingStep[] }[];
  decisions: FindingDecision[];
}

export interface ReportTable {
  columns: string[];
  rows: string[][];
  caption: string;
}

export interface ReportItem {
  finding: string | null;
  title: string;
  category: string;
  severity: string;
  flow: { sources: string[]; sink: string | null; sink_url: string | null; destination: string | null };
  evidence: { file: string; line: number; note?: string; url: string | null }[];
  decisions: { question: string; answer: string; probability: number }[];
  instructions: string[];
}

export interface ReportSection {
  id: string;
  title: string;
  basis: string;
  intro: string;
  narrative: { sentences: string[]; drafter: string } | null;
  tables: ReportTable[];
  notes: string[];
  items: ReportItem[];
  children: ReportSection[];
}

export interface ReportFinding {
  id: string;
  title: string;
  severity: Severity;
  likelihood: string;
  measure: string;
}

export interface ReportJson {
  schema: string;
  run: { repo: string; commit: string; run_id: string; generated_at: string };
  summary: {
    findings: number;
    unresolved: number;
    provider: string;
    question_set_version: string;
    threshold: number;
    narrative: string[];
  };
  sections: ReportSection[];
  findings: ReportFinding[];
}

export interface FindingsDiff {
  before: RunSummary;
  after: RunSummary;
  new: Finding[];
  resolved: Finding[];
  changed_severity: { key: string; before: Finding; after: Finding }[];
  changed_status: { key: string; before: Finding; after: Finding }[];
  changed_classification: { key: string; before: Finding; after: Finding }[];
  unchanged: number;
}

export interface Settings {
  threshold: number;
  question_set_version: string | null;
  question_set_versions: string[];
  registry_version: string;
  severity_version: string;
  statutes_version: string;
  decision_provider: string;
  runs_per_hour: number;
  run_timeout_s: number;
  clone_max_mb: number;
}

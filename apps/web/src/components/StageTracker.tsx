import type { Stage } from "../api/types";

const LABELS: Record<string, string> = {
  clone: "Clone",
  parse: "Parse",
  graph: "Graph",
  registry: "Registry",
  classify: "Classify",
  verify: "Verify",
  report: "Report",
};

const MARK: Record<string, { symbol: string; className: string; text: string }> = {
  done: { symbol: "✓", className: "border-ink bg-ink text-paper", text: "done" },
  running: { symbol: "", className: "border-accent bg-surface ring-4 ring-accent/15", text: "running" },
  failed: { symbol: "✕", className: "border-critical bg-critical text-white", text: "failed" },
  skipped: { symbol: "–", className: "border-dashed border-line-strong bg-surface text-ink-3", text: "skipped" },
  pending: { symbol: "", className: "border-line-strong bg-surface", text: "pending" },
};

function duration(stage: Stage): string {
  if (!stage.started_at || !stage.finished_at) return "";
  const ms = new Date(stage.finished_at).getTime() - new Date(stage.started_at).getTime();
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function StageTracker({ stages }: { stages: Stage[] }) {
  return (
    <ol className="grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-7 sm:gap-0" aria-label="Run stages">
      {stages.map((stage, index) => {
        const mark = MARK[stage.status] ?? MARK.pending!;
        const done = stage.status === "done";
        return (
          <li key={stage.stage} className="relative flex items-center gap-3 sm:flex-col sm:items-start sm:gap-2.5">
            {index > 0 && (
              <span
                aria-hidden
                className={`absolute top-[11px] right-[calc(100%-4px)] hidden h-px w-[calc(100%-28px)] sm:block ${
                  done || stage.status === "running" ? "bg-ink" : "bg-line-strong"
                }`}
              />
            )}
            <span
              aria-hidden
              className={`relative z-[1] inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold ${mark.className}`}
            >
              {stage.status === "running" ? <span className="h-2 w-2 animate-pulse rounded-full bg-accent" /> : mark.symbol}
            </span>
            <span className="leading-tight">
              <span className="text-sm font-medium text-ink">{LABELS[stage.stage] ?? stage.stage}</span>
              <span className="sr-only">: {mark.text}</span>
              <span className="num block text-xs text-ink-3">{done ? duration(stage) : mark.text}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

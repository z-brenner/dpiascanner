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
  done: { symbol: "✓", className: "bg-emerald-700 text-white", text: "done" },
  running: { symbol: "•", className: "bg-sky-700 text-white animate-pulse", text: "running" },
  failed: { symbol: "✕", className: "bg-red-700 text-white", text: "failed" },
  skipped: { symbol: "–", className: "bg-stone-400 text-white dark:bg-stone-600", text: "skipped" },
  pending: { symbol: "", className: "border border-stone-400 dark:border-stone-600", text: "pending" },
};

function duration(stage: Stage): string {
  if (!stage.started_at || !stage.finished_at) return "";
  const ms = new Date(stage.finished_at).getTime() - new Date(stage.started_at).getTime();
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function StageTracker({ stages }: { stages: Stage[] }) {
  return (
    <ol className="flex flex-wrap gap-x-6 gap-y-3" aria-label="Run stages">
      {stages.map((stage) => {
        const mark = MARK[stage.status] ?? MARK.pending!;
        return (
          <li key={stage.stage} className="flex items-center gap-2 text-sm">
            <span
              aria-hidden
              className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-xs ${mark.className}`}
            >
              {mark.symbol}
            </span>
            <span>
              <span className="font-medium">{LABELS[stage.stage] ?? stage.stage}</span>
              <span className="sr-only">: {mark.text}</span>
              <span className="block text-xs text-stone-500">{stage.status === "done" ? duration(stage) : mark.text}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

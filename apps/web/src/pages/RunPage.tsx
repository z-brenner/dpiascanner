import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Run } from "../api/types";
import { Pill, button, primaryButton } from "../components/Badge";
import { StageTracker } from "../components/StageTracker";

const ACTIVE = new Set(["queued", "running", "cancelling"]);

export function RunPage() {
  const { runId = "" } = useParams();
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = () => {
      api
        .run(runId)
        .then((r) => {
          if (!live) return;
          setRun(r);
          if (ACTIVE.has(r.status)) timer = setTimeout(poll, 2000);
        })
        .catch((e: unknown) => live && setError(e instanceof Error ? e.message : "Could not load the run"));
    };
    poll();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [runId]);

  if (error) return <p className="text-red-700">{error}</p>;
  if (!run) return <p className="text-sm">Loading…</p>;
  const coverage = run.coverage;
  const tone = run.status === "completed" ? "good" : run.status === "failed" ? "bad" : "neutral";
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">
          <span className="font-mono">{run.repo}</span> @ <span className="font-mono">{run.sha.slice(0, 12)}</span>
        </h1>
        <Pill tone={tone}>{run.status}</Pill>
        {run.trigger === "pull_request" && <Pill>PR #{run.pr_number}</Pill>}
        {ACTIVE.has(run.status) && run.status !== "cancelling" && (
          <button type="button" className={button} onClick={() => void api.cancel(run.id).then(() => setRun({ ...run, status: "cancelling" }))}>
            Cancel run
          </button>
        )}
      </div>
      <StageTracker stages={run.stages} />
      {run.error && (
        <div className="rounded border border-red-300 bg-red-50 p-3 text-sm dark:border-red-800 dark:bg-red-950">
          <p className="font-medium">Failed during {run.error.stage}</p>
          <pre className="mt-1 whitespace-pre-wrap font-mono text-xs">{run.error.message}</pre>
        </div>
      )}
      {coverage && coverage.tainted_paths !== undefined && (
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-stone-500">Tainted paths</dt>
            <dd className="text-lg font-semibold">{coverage.tainted_paths}</dd>
          </div>
          <div>
            <dt className="text-stone-500">Fully resolved</dt>
            <dd className="text-lg font-semibold">{((coverage.resolved_fraction ?? 1) * 100).toFixed(1)}%</dd>
          </div>
          <div>
            <dt className="text-stone-500">Findings</dt>
            <dd className="text-lg font-semibold">{run.findings ?? "–"}</dd>
          </div>
          <div>
            <dt className="text-stone-500">Unresolved</dt>
            <dd className="text-lg font-semibold">{run.unresolved ?? "–"}</dd>
          </div>
        </dl>
      )}
      {run.status === "completed" && (
        <Link className={primaryButton} to={`/runs/${run.id}/report`}>
          Open the report
        </Link>
      )}
    </div>
  );
}

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Run } from "../api/types";
import { Pill, button, primaryButton } from "../components/Badge";
import { StageTracker } from "../components/StageTracker";
import { ErrorText, Loading, Notice, PageHeader, StatTile } from "../components/ui";

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

  if (error) return <ErrorText>{error}</ErrorText>;
  if (!run) return <Loading what="Loading the run" />;
  const coverage = run.coverage;
  const tone = run.status === "completed" ? "good" : run.status === "failed" ? "bad" : "neutral";
  return (
    <div className="space-y-10">
      <PageHeader
        eyebrow={
          <>
            Run · <span className="normal-case">{run.sha.slice(0, 12)}</span>
          </>
        }
        title={<span className="break-all">{run.repo}</span>}
        actions={
          <>
            <Pill tone={tone}>{run.status}</Pill>
            {run.trigger === "pull_request" && <Pill>PR #{run.pr_number}</Pill>}
            {ACTIVE.has(run.status) && run.status !== "cancelling" && (
              <button
                type="button"
                className={button}
                onClick={() => void api.cancel(run.id).then(() => setRun({ ...run, status: "cancelling" }))}
              >
                Cancel run
              </button>
            )}
          </>
        }
      >
        {run.ref && (
          <span>
            <span className="font-mono text-[13px]">{run.ref}</span>, {run.mode === "incremental" ? "incremental" : "full"}{" "}
            scan, started {run.trigger === "manual" ? "by hand" : `by a ${run.trigger.replace("_", " ")}`}.
          </span>
        )}
      </PageHeader>

      <section className="card p-5 sm:p-6">
        <StageTracker stages={run.stages} />
      </section>

      {run.error && (
        <Notice tone="bad" title={`Failed during ${run.error.stage ?? "the run"}`}>
          <pre className="mt-1 font-mono text-xs whitespace-pre-wrap">{run.error.message}</pre>
        </Notice>
      )}

      {coverage && coverage.tainted_paths !== undefined && (
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile label="Tainted paths" value={coverage.tainted_paths} />
          <StatTile label="Fully resolved" value={`${((coverage.resolved_fraction ?? 1) * 100).toFixed(1)}%`} />
          <StatTile label="Findings" value={run.findings ?? "–"} />
          <StatTile label="Unresolved" value={run.unresolved ?? "–"} note="Need a reviewer's answer" />
        </dl>
      )}

      {run.status === "completed" && (
        <div className="card flex flex-wrap items-center gap-4 p-5">
          <div className="flex-1">
            <p className="display text-xl">The assessment is ready.</p>
            <p className="text-sm text-ink-2">Findings, the DPIA draft, and every path behind them.</p>
          </div>
          <Link className={primaryButton} to={`/runs/${run.id}/report`}>
            Open the report
          </Link>
        </div>
      )}
    </div>
  );
}

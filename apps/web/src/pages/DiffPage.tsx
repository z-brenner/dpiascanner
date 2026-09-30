import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Finding, FindingsDiff } from "../api/types";
import { SeverityBadge, button, input } from "../components/Badge";
import { useLoad } from "../components/Layout";
import { Notice, PageHeader } from "../components/ui";

function Rows({ title, findings, mark }: { title: string; findings: Finding[]; mark: string }) {
  return (
    <section className="card overflow-hidden">
      <h2 className="flex items-baseline justify-between border-b border-line px-4 py-3">
        <span className="flex items-center gap-2 text-sm font-semibold text-ink">
          <span aria-hidden className="font-mono text-ink-3">
            {mark}
          </span>
          {title}
        </span>
        <span className="num text-xs text-ink-3">{findings.length}</span>
      </h2>
      {findings.length === 0 ? (
        <p className="px-4 py-4 text-sm text-ink-3">None.</p>
      ) : (
        <ul className="divide-y divide-line text-sm">
          {findings.map((f) => (
            <li key={f.key} className="space-y-1 px-4 py-3">
              <div className="flex items-center gap-3">
                <span className="font-mono text-xs text-ink-3">{f.id}</span>
                <SeverityBadge severity={f.severity} />
              </div>
              <p className="text-ink">{f.title}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function DiffPage() {
  const { owner = "", repo = "" } = useParams();
  const runs = useLoad(() => api.repoRuns(owner, repo), [owner, repo]);
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [diff, setDiff] = useState<FindingsDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const completed = (runs.data?.items ?? []).filter((r) => r.status === "completed");
  const label = (id: string) => {
    const r = completed.find((x) => x.id === id);
    return r ? `${r.sha.slice(0, 12)} · ${r.trigger} · ${r.created_at?.slice(0, 16).replace("T", " ")}` : id;
  };
  return (
    <div className="space-y-10">
      <PageHeader eyebrow="Compare runs" title={<span className="break-all">{`${owner}/${repo}`}</span>}>
        Pick two completed runs to see what is new, what was resolved, and what changed between them.
      </PageHeader>
      <div className="card flex flex-wrap items-end gap-4 p-5 text-sm">
        {([["Before", a, setA], ["After", b, setB]] as const).map(([name, value, set]) => (
          <label key={name} className="flex min-w-56 flex-1 flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-2">{name}</span>
            <select className={`${input} font-mono text-[13px]`} value={value} onChange={(e) => set(e.target.value)}>
              <option value="">Choose a run</option>
              {completed.map((r) => (
                <option key={r.id} value={r.id}>
                  {label(r.id)}
                </option>
              ))}
            </select>
          </label>
        ))}
        <button
          type="button"
          className={button}
          disabled={!a || !b || a === b}
          onClick={() =>
            api
              .diff(a, b)
              .then(setDiff)
              .catch((e: unknown) => setError(e instanceof Error ? e.message : "Diff failed"))
          }
        >
          Compare
        </button>
      </div>
      {error && <Notice tone="bad">{error}</Notice>}
      {diff && (
        <div className="space-y-4">
          <p className="text-sm text-ink-2">{diff.unchanged} unchanged findings.</p>
          <div className="grid gap-4 lg:grid-cols-3">
            <Rows title="New" mark="+" findings={diff.new} />
            <Rows title="Resolved" mark="−" findings={diff.resolved} />
            <Rows
              title="Changed"
              mark="~"
              findings={[...diff.changed_severity, ...diff.changed_status, ...diff.changed_classification].map((c) => c.after)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Finding, FindingsDiff } from "../api/types";
import { SeverityBadge, button, input } from "../components/Badge";
import { useLoad } from "../components/Layout";

function Rows({ title, findings }: { title: string; findings: Finding[] }) {
  return (
    <section>
      <h2 className="font-semibold">
        {title} ({findings.length})
      </h2>
      <ul className="text-sm">
        {findings.map((f) => (
          <li key={f.key} className="flex gap-2 py-1">
            <span className="font-mono">{f.id}</span> <SeverityBadge severity={f.severity} /> {f.title}
          </li>
        ))}
      </ul>
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
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">
        Compare runs of <span className="font-mono">{owner}/{repo}</span>
      </h1>
      <div className="flex flex-wrap items-end gap-3 text-sm">
        {([["Before", a, setA], ["After", b, setB]] as const).map(([name, value, set]) => (
          <label key={name} className="flex flex-col gap-1">
            {name}
            <select className={input} value={value} onChange={(e) => set(e.target.value)}>
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
      {error && <p className="text-red-700">{error}</p>}
      {diff && (
        <div className="space-y-4">
          <p className="text-sm">{diff.unchanged} unchanged findings.</p>
          <Rows title="New" findings={diff.new} />
          <Rows title="Resolved" findings={diff.resolved} />
          <Rows
            title="Changed"
            findings={[...diff.changed_severity, ...diff.changed_status, ...diff.changed_classification].map((c) => c.after)}
          />
        </div>
      )}
    </div>
  );
}

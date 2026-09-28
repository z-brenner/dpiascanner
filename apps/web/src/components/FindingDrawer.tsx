import { useEffect, useState } from "react";
import type { FindingDetail, ReportFinding } from "../api/types";
import { Pill, SeverityBadge, button } from "./Badge";

interface Props {
  findingId: string;
  load: (findingId: string) => Promise<FindingDetail>;
  reportEntry?: ReportFinding;
  onClose: () => void;
}

const KIND_ORDER = { source: 0, transform: 1, sink: 2, retention: 3 } as const;

export function FindingDrawer({ findingId, load, reportEntry, onClose }: Props) {
  const [detail, setDetail] = useState<FindingDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setDetail(null);
    setError(null);
    load(findingId)
      .then((d) => live && setDetail(d))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : "Could not load the finding."));
    return () => {
      live = false;
    };
  }, [findingId, load]);

  const nodes = detail ? [...detail.nodes].sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind]) : [];
  return (
    <aside
      role="dialog"
      aria-label={`Finding ${findingId}`}
      className="fixed inset-y-0 right-0 z-10 w-full max-w-2xl overflow-y-auto border-l border-stone-300 bg-white p-4 shadow-xl dark:border-stone-700 dark:bg-stone-950"
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <h2 className="text-lg font-semibold">
          <span className="font-mono">{findingId}</span> {detail?.title}
        </h2>
        <button type="button" className={button} onClick={onClose}>
          Close
        </button>
      </div>
      {error && <p className="text-red-700">{error}</p>}
      {!detail && !error && <p className="text-sm text-stone-500">Loading…</p>}
      {detail && (
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={detail.severity} />
            <Pill tone={detail.status === "unresolved" ? "warn" : "neutral"}>{detail.status}</Pill>
            <Pill tone={detail.verification === "verified" ? "good" : "neutral"}>
              {detail.verification === "verified" ? "verified dynamically" : "inferred from code"}
            </Pill>
            {reportEntry && <span>likelihood: {reportEntry.likelihood}</span>}
          </div>
          <p>
            Data: {detail.data_categories.join(", ") || "–"}; destination: {detail.destination_class ?? "–"}; statutes:{" "}
            {detail.statute_refs.join(", ") || "–"}
          </p>
          {reportEntry && (
            <p>
              <span className="font-medium">Measure:</span> {reportEntry.measure}
            </p>
          )}
          <section>
            <h3 className="font-semibold">Path</h3>
            <ol className="space-y-2">
              {nodes.map((n) => (
                <li key={n.id}>
                  <p>
                    <Pill>{n.kind}</Pill> <code>{n.symbol}</code> at{" "}
                    {n.url ? (
                      <a className="underline" href={n.url} target="_blank" rel="noreferrer">
                        {n.file}:{n.line_start}
                      </a>
                    ) : (
                      <code>
                        {n.file}:{n.line_start}
                      </code>
                    )}
                  </p>
                  {n.snippet && (
                    <pre className="mt-1 overflow-x-auto rounded bg-stone-100 p-2 font-mono text-xs dark:bg-stone-900">
                      {n.snippet}
                    </pre>
                  )}
                </li>
              ))}
            </ol>
          </section>
          {detail.edges.length > 0 && (
            <section>
              <h3 className="font-semibold">Evidence steps</h3>
              {detail.edges.map((e) => (
                <ol key={e.id} className="ml-5 list-decimal">
                  {e.steps.map((s, i) => (
                    <li key={i}>
                      {s.url ? (
                        <a className="underline" href={s.url} target="_blank" rel="noreferrer">
                          {s.file}:{s.line}
                        </a>
                      ) : (
                        `${s.file}:${s.line}`
                      )}{" "}
                      {s.kind}
                      {s.function ? ` in ${s.function}` : ""}
                      {s.unresolved && <span className="ml-1 text-amber-700 dark:text-amber-300">unresolved: {s.note}</span>}
                    </li>
                  ))}
                </ol>
              ))}
            </section>
          )}
          <section>
            <h3 className="font-semibold">Decisions</h3>
            <table className="w-full text-left text-xs">
              <thead>
                <tr>
                  <th className="p-1">Question</th>
                  <th className="p-1">Answer</th>
                  <th className="p-1">Probability</th>
                </tr>
              </thead>
              <tbody>
                {detail.decisions.map((d) => (
                  <tr key={d.id} className="border-t border-stone-200 dark:border-stone-800">
                    <td className="p-1 font-mono">{d.question}</td>
                    <td className="p-1">{d.answer}</td>
                    <td className="p-1">
                      <span className="inline-block h-2 bg-sky-700 align-middle" style={{ width: `${Math.round(d.probability * 60)}px` }} />{" "}
                      {d.probability.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>
      )}
    </aside>
  );
}

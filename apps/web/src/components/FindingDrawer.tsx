import { useEffect, useState } from "react";
import type { FindingDetail, ReportFinding } from "../api/types";
import { Pill, SeverityBadge, ghostButton } from "./Badge";
import { Loading, Meter } from "./ui";

interface Props {
  findingId: string;
  load: (findingId: string) => Promise<FindingDetail>;
  reportEntry?: ReportFinding;
  onClose: () => void;
}

const KIND_ORDER = { source: 0, transform: 1, sink: 2, retention: 3 } as const;

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[7.5rem_1fr] gap-3 py-2">
      <dt className="text-ink-3">{label}</dt>
      <dd className="text-ink">{children}</dd>
    </div>
  );
}

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

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const nodes = detail ? [...detail.nodes].sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind]) : [];
  return (
    <div className="fixed inset-0 z-30">
      <div aria-hidden className="absolute inset-0 bg-ink/20 backdrop-blur-[1px]" onClick={onClose} />
      <aside
        role="dialog"
        aria-label={`Finding ${findingId}`}
        className="absolute inset-y-0 right-0 flex w-full max-w-2xl flex-col border-l border-line bg-paper shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-line px-6 py-5">
          <div className="min-w-0 space-y-1">
            <p className="eyebrow">Finding · {findingId}</p>
            <h2 className="display text-2xl leading-tight">{detail?.title ?? "…"}</h2>
          </div>
          <button type="button" className={ghostButton} onClick={onClose}>
            Close
          </button>
        </div>
        <div className="flex-1 space-y-7 overflow-y-auto px-6 py-6 text-sm">
          {error && <p className="text-critical">{error}</p>}
          {!detail && !error && <Loading />}
          {detail && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <SeverityBadge severity={detail.severity} />
                <Pill tone={detail.status === "unresolved" ? "warn" : "neutral"}>{detail.status}</Pill>
                <Pill tone={detail.verification === "verified" ? "good" : "neutral"}>
                  {detail.verification === "verified" ? "verified dynamically" : "inferred from code"}
                </Pill>
              </div>

              <dl className="divide-y divide-line border-y border-line">
                <Fact label="Data">{detail.data_categories.join(", ").replace(/_/g, " ") || "–"}</Fact>
                <Fact label="Destination">{detail.destination_class?.replace(/_/g, " ") ?? "–"}</Fact>
                {reportEntry && <Fact label="Likelihood">{reportEntry.likelihood}</Fact>}
                <Fact label="Statutes">
                  <span className="font-mono text-xs leading-relaxed text-ink-2">{detail.statute_refs.join(" · ") || "–"}</span>
                </Fact>
              </dl>

              {reportEntry && (
                <div className="rounded-lg bg-sunken p-4">
                  <p className="eyebrow">Measure</p>
                  <p className="mt-1 leading-relaxed text-ink first-letter:uppercase">{reportEntry.measure}</p>
                </div>
              )}

              <section className="space-y-3">
                <h3 className="eyebrow">Path</h3>
                <ol className="relative space-y-4 border-l border-line-strong pl-5">
                  {nodes.map((n) => (
                    <li key={n.id} className="relative">
                      <span
                        aria-hidden
                        className={`absolute top-1.5 -left-[25px] h-2.5 w-2.5 rounded-full border-2 border-paper ${
                          n.kind === "sink" ? "bg-ink" : n.kind === "source" ? "bg-accent" : "bg-ink-3"
                        }`}
                      />
                      <p className="flex flex-wrap items-baseline gap-x-2">
                        <span className="font-mono text-[11px] tracking-wider text-ink-3 uppercase">{n.kind}</span>
                        <code className="text-[13px] text-ink">{n.symbol}</code>
                      </p>
                      <p className="mt-0.5 font-mono text-xs">
                        {n.url ? (
                          <a className="link text-ink-2" href={n.url} target="_blank" rel="noreferrer">
                            {n.file}:{n.line_start}
                          </a>
                        ) : (
                          <span className="text-ink-2">
                            {n.file}:{n.line_start}
                          </span>
                        )}
                      </p>
                      {n.snippet && (
                        <pre className="mt-2 overflow-x-auto rounded-lg border border-line bg-surface p-3 text-xs leading-relaxed text-ink-2">
                          {n.snippet}
                        </pre>
                      )}
                    </li>
                  ))}
                </ol>
              </section>

              {detail.edges.length > 0 && (
                <details className="group">
                  <summary className="eyebrow cursor-pointer list-none">
                    <span className="group-open:hidden">Show</span>
                    <span className="hidden group-open:inline">Hide</span> evidence steps
                  </summary>
                  <div className="mt-3 space-y-3">
                    {detail.edges.map((e) => (
                      <ol key={e.id} className="ml-5 list-decimal space-y-1 text-[13px] text-ink-2 marker:text-ink-3">
                        {e.steps.map((s, i) => (
                          <li key={i}>
                            {s.url ? (
                              <a className="link font-mono text-xs" href={s.url} target="_blank" rel="noreferrer">
                                {s.file}:{s.line}
                              </a>
                            ) : (
                              <span className="font-mono text-xs">
                                {s.file}:{s.line}
                              </span>
                            )}{" "}
                            {s.kind}
                            {s.function ? ` in ${s.function}` : ""}
                            {s.unresolved && <span className="ml-1 text-ink">· unresolved: {s.note}</span>}
                          </li>
                        ))}
                      </ol>
                    ))}
                  </div>
                </details>
              )}

              <section className="space-y-3">
                <h3 className="eyebrow">Decisions</h3>
                <div className="card overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="border-b border-line text-ink-3">
                      <tr>
                        <th className="px-3 py-2 font-medium">Question</th>
                        <th className="px-3 py-2 font-medium">Answer</th>
                        <th className="px-3 py-2 font-medium">Probability</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {detail.decisions.map((d) => (
                        <tr key={d.id}>
                          <td className="px-3 py-2 font-mono text-ink-2">{d.question}</td>
                          <td className="px-3 py-2 text-ink">{d.answer}</td>
                          <td className="px-3 py-2">
                            <Meter value={d.probability} label={`Probability of ${d.answer}`} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

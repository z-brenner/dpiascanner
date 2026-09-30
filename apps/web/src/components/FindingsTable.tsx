import { useMemo, useState } from "react";
import type { Finding, Severity } from "../api/types";
import { Pill, SeverityBadge, ghostButton, input } from "./Badge";
import { EMPTY_FILTERS, SEVERITIES, categoryLabel, filterFindings, isVerified, type FindingFilters } from "./filters";

interface Props {
  findings: Finding[];
  onSelect: (finding: Finding) => void;
  selectedId?: string | null;
}

const th = "px-4 py-2.5 text-xs font-medium text-ink-3";

export function FindingsTable({ findings, onSelect, selectedId }: Props) {
  const [filters, setFilters] = useState<FindingFilters>(EMPTY_FILTERS);
  const categories = useMemo(() => [...new Set(findings.map((f) => f.category))].sort(), [findings]);
  const shown = useMemo(() => filterFindings(findings, filters), [findings, filters]);

  function toggleSeverity(severity: Severity) {
    setFilters((f) => ({
      ...f,
      severities: f.severities.includes(severity) ? f.severities.filter((s) => s !== severity) : [...f.severities, severity],
    }));
  }

  return (
    <section aria-label="Findings" className="space-y-4">
      <div className="flex flex-wrap items-end gap-x-5 gap-y-4 text-sm">
        <label className="flex min-w-44 flex-col gap-1.5">
          <span className="text-xs font-medium text-ink-2">Category</span>
          <select
            className={input}
            value={filters.category}
            onChange={(e) => setFilters((f) => ({ ...f, category: e.target.value }))}
          >
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {categoryLabel(c)}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="flex flex-col gap-1.5">
          <legend className="mb-1.5 text-xs font-medium text-ink-2">Severity</legend>
          <div className="flex flex-wrap gap-1.5">
            {SEVERITIES.map((s) => {
              const on = filters.severities.includes(s);
              return (
                <label
                  key={s}
                  className={`inline-flex cursor-pointer items-center rounded-lg border px-2.5 py-[7px] transition-colors ${
                    on ? "border-ink bg-sunken text-ink" : "border-line-strong bg-surface text-ink-2 hover:text-ink"
                  }`}
                >
                  <input type="checkbox" className="sr-only" checked={on} onChange={() => toggleSeverity(s)} />
                  <SeverityBadge severity={s} />
                </label>
              );
            })}
          </div>
        </fieldset>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-ink-2">Status</span>
          <select
            className={input}
            value={filters.status}
            onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value as FindingFilters["status"] }))}
          >
            <option value="all">Resolved and unresolved</option>
            <option value="resolved">Resolved</option>
            <option value="unresolved">Unresolved</option>
          </select>
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-ink-2">Evidence</span>
          <select
            className={input}
            value={filters.verification}
            onChange={(e) => setFilters((f) => ({ ...f, verification: e.target.value as FindingFilters["verification"] }))}
          >
            <option value="all">Verified and inferred</option>
            <option value="verified">Verified dynamically</option>
            <option value="inferred">Inferred from code</option>
          </select>
        </label>
        <button type="button" className={ghostButton} onClick={() => setFilters(EMPTY_FILTERS)}>
          Clear filters
        </button>
      </div>
      <p className="text-xs text-ink-3" aria-live="polite">
        {shown.length} of {findings.length} findings
      </p>
      <div className="card overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="border-b border-line bg-sunken/60">
            <tr>
              <th className={th}>Finding</th>
              <th className={th}>Severity</th>
              <th className={th}>Title</th>
              <th className={th}>Data</th>
              <th className={th}>Status</th>
              <th className={th}>Evidence</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {shown.map((f) => (
              <tr
                key={f.id}
                className={`cursor-pointer transition-colors hover:bg-sunken/70 ${selectedId === f.id ? "bg-sunken" : ""}`}
                onClick={() => onSelect(f)}
              >
                <td className="px-4 py-3 align-top font-mono text-[13px]">
                  <button
                    type="button"
                    className="link text-ink"
                    onClick={(event) => {
                      event.stopPropagation();
                      onSelect(f);
                    }}
                  >
                    {f.id}
                  </button>
                </td>
                <td className="px-4 py-3 align-top">
                  <SeverityBadge severity={f.severity} />
                </td>
                <td className="px-4 py-3 align-top font-medium text-ink">{f.title}</td>
                <td className="px-4 py-3 align-top text-ink-2">{f.data_categories.join(", ").replace(/_/g, " ") || "–"}</td>
                <td className="px-4 py-3 align-top">
                  <Pill tone={f.status === "unresolved" ? "warn" : "neutral"}>{f.status}</Pill>
                </td>
                <td className="px-4 py-3 align-top">
                  <Pill tone={isVerified(f) ? "good" : "neutral"}>{isVerified(f) ? "verified" : "inferred"}</Pill>
                </td>
              </tr>
            ))}
            {shown.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-ink-3">
                  No findings match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

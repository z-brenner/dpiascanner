import { useMemo, useState } from "react";
import type { Finding, Severity } from "../api/types";
import { Pill, SeverityBadge, input } from "./Badge";
import { EMPTY_FILTERS, SEVERITIES, categoryLabel, filterFindings, isVerified, type FindingFilters } from "./filters";

interface Props {
  findings: Finding[];
  onSelect: (finding: Finding) => void;
  selectedId?: string | null;
}

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
    <section aria-label="Findings" className="space-y-3">
      <div className="flex flex-wrap items-end gap-4 text-sm">
        <label className="flex flex-col gap-1">
          <span className="font-medium">Category</span>
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
        <fieldset className="flex flex-col gap-1">
          <legend className="font-medium">Severity</legend>
          <div className="flex flex-wrap gap-2">
            {SEVERITIES.map((s) => (
              <label key={s} className="flex items-center gap-1">
                <input type="checkbox" checked={filters.severities.includes(s)} onChange={() => toggleSeverity(s)} />
                {s}
              </label>
            ))}
          </div>
        </fieldset>
        <label className="flex flex-col gap-1">
          <span className="font-medium">Status</span>
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
        <label className="flex flex-col gap-1">
          <span className="font-medium">Evidence</span>
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
        <button type="button" className="text-sky-800 underline dark:text-sky-300" onClick={() => setFilters(EMPTY_FILTERS)}>
          Clear filters
        </button>
      </div>
      <p className="text-sm text-stone-600 dark:text-stone-400" aria-live="polite">
        {shown.length} of {findings.length} findings
      </p>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-stone-300 dark:border-stone-700">
              <th className="p-2">Finding</th>
              <th className="p-2">Severity</th>
              <th className="p-2">Title</th>
              <th className="p-2">Data</th>
              <th className="p-2">Status</th>
              <th className="p-2">Evidence</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((f) => (
              <tr
                key={f.id}
                className={`cursor-pointer border-b border-stone-200 hover:bg-stone-100 dark:border-stone-800 dark:hover:bg-stone-900 ${
                  selectedId === f.id ? "bg-sky-50 dark:bg-sky-950" : ""
                }`}
                onClick={() => onSelect(f)}
              >
                <td className="p-2 font-mono">
                  <button
                    type="button"
                    className="underline"
                    onClick={(event) => {
                      event.stopPropagation();
                      onSelect(f);
                    }}
                  >
                    {f.id}
                  </button>
                </td>
                <td className="p-2">
                  <SeverityBadge severity={f.severity} />
                </td>
                <td className="p-2">{f.title}</td>
                <td className="p-2">{f.data_categories.join(", ") || "–"}</td>
                <td className="p-2">
                  <Pill tone={f.status === "unresolved" ? "warn" : "neutral"}>{f.status}</Pill>
                </td>
                <td className="p-2">
                  <Pill tone={isVerified(f) ? "good" : "neutral"}>{isVerified(f) ? "verified" : "inferred"}</Pill>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

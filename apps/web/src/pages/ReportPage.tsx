import { useCallback, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { DownloadMenu } from "../components/DownloadMenu";
import { FindingDrawer } from "../components/FindingDrawer";
import { FindingsTable } from "../components/FindingsTable";
import { useLoad } from "../components/Layout";
import { ReportSections } from "../components/ReportSections";

export function ReportPage() {
  const { runId = "" } = useParams();
  const report = useLoad(() => api.report(runId), [runId]);
  const findings = useLoad(() => api.findings(runId), [runId]);
  const [selected, setSelected] = useState<string | null>(null);
  const load = useCallback((fid: string) => api.finding(runId, fid), [runId]);
  const entries = useMemo(() => new Map((report.data?.findings ?? []).map((f) => [f.id, f])), [report.data]);

  if (report.error || findings.error) return <p className="text-red-700">{report.error ?? findings.error}</p>;
  if (!report.data || !findings.data) return <p className="text-sm">Loading…</p>;
  const r = report.data;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start gap-3">
        <div>
          <h1 className="text-xl font-semibold">Data protection impact assessment</h1>
          <p className="text-sm text-stone-600 dark:text-stone-400">
            <span className="font-mono">{r.run.repo}</span> at <span className="font-mono">{r.run.commit.slice(0, 12)}</span> ·
            provider {r.summary.provider} · question set {r.summary.question_set_version} · threshold {r.summary.threshold} ·
            narrative {r.summary.narrative.join(", ")}
          </p>
        </div>
        <div className="ml-auto">
          <DownloadMenu runId={runId} />
        </div>
      </div>
      <FindingsTable findings={findings.data.items} selectedId={selected} onSelect={(f) => setSelected(f.id)} />
      <ReportSections sections={r.sections} onCite={setSelected} />
      {selected && <FindingDrawer findingId={selected} load={load} reportEntry={entries.get(selected)} onClose={() => setSelected(null)} />}
    </div>
  );
}

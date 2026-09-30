import { useCallback, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { DownloadMenu } from "../components/DownloadMenu";
import { FindingDrawer } from "../components/FindingDrawer";
import { FindingsTable } from "../components/FindingsTable";
import { useLoad } from "../components/Layout";
import { ReportSections } from "../components/ReportSections";
import { ErrorText, Loading, PageHeader, SectionTitle, StatTile } from "../components/ui";

export function ReportPage() {
  const { runId = "" } = useParams();
  const report = useLoad(() => api.report(runId), [runId]);
  const findings = useLoad(() => api.findings(runId), [runId]);
  const [selected, setSelected] = useState<string | null>(null);
  const load = useCallback((fid: string) => api.finding(runId, fid), [runId]);
  const entries = useMemo(() => new Map((report.data?.findings ?? []).map((f) => [f.id, f])), [report.data]);

  if (report.error || findings.error) return <ErrorText>{report.error ?? findings.error}</ErrorText>;
  if (!report.data || !findings.data) return <Loading what="Loading the assessment" />;
  const r = report.data;
  const items = findings.data.items;
  const count = (predicate: (f: (typeof items)[number]) => boolean) => items.filter(predicate).length;
  return (
    <div className="space-y-12">
      <PageHeader
        eyebrow={
          <>
            DPIA ·{" "}
            <span className="normal-case">
              {r.run.repo} @ {r.run.commit.slice(0, 12)}
            </span>
          </>
        }
        title="Data protection impact assessment"
        actions={<DownloadMenu runId={runId} />}
      >
        <p className="font-mono text-xs leading-relaxed text-ink-3">
          provider {r.summary.provider} · question set {r.summary.question_set_version} · threshold {r.summary.threshold}{" "}
          · narrative {r.summary.narrative.join(", ")}
        </p>
      </PageHeader>

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Findings" value={items.length} />
        <StatTile label="Critical" value={count((f) => f.severity === "critical")} />
        <StatTile label="High" value={count((f) => f.severity === "high")} />
        <StatTile label="Unresolved" value={count((f) => f.status === "unresolved")} note="Need a reviewer's answer" />
      </dl>

      <section className="space-y-4">
        <SectionTitle>Findings</SectionTitle>
        <FindingsTable findings={items} selectedId={selected} onSelect={(f) => setSelected(f.id)} />
      </section>

      <section className="space-y-2">
        <SectionTitle>Assessment</SectionTitle>
        <ReportSections sections={r.sections} onCite={setSelected} />
      </section>

      {selected && (
        <FindingDrawer
          findingId={selected}
          load={load}
          reportEntry={entries.get(selected)}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

import type { ReportSection, ReportTable } from "../api/types";
import { CitedText } from "./Citations";

interface Props {
  sections: ReportSection[];
  onCite: (findingId: string) => void;
}

function Table({ table, onCite }: { table: ReportTable; onCite: (id: string) => void }) {
  if (!table.rows.length) return <p className="text-sm text-ink-3">{table.caption || "Table"}: nothing to list.</p>;
  return (
    <div className="card my-4 overflow-x-auto">
      <table className="w-full border-collapse text-left text-[13px]">
        {table.caption && <caption className="px-4 pt-3 pb-1 text-left text-xs text-ink-3">{table.caption}</caption>}
        <thead className="border-b border-line">
          <tr>
            {table.columns.map((c) => (
              <th key={c} className="px-4 py-2.5 text-xs font-medium text-ink-3">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {table.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className="px-4 py-2.5 align-top text-ink-2">
                  <CitedText text={cell} onCite={onCite} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Section({ section, level, onCite }: { section: ReportSection; level: number; onCite: (id: string) => void }) {
  const Heading = (level === 2 ? "h2" : level === 3 ? "h3" : "h4") as "h2";
  const heading =
    level === 2
      ? "display border-t border-line pt-8 text-[1.75rem] leading-tight"
      : level === 3
        ? "display pt-2 text-xl"
        : "pt-1 text-sm font-semibold text-ink";
  return (
    <section id={`sec-${section.id}`} className={`space-y-3 ${level === 2 ? "pt-4" : ""}`}>
      <Heading className={heading}>{section.title}</Heading>
      {section.basis && <p className="font-mono text-[11px] tracking-wide text-ink-3">{section.basis}</p>}
      {section.intro && <p className="max-w-3xl text-[15px] leading-7 text-ink-2">{section.intro}</p>}
      {section.narrative && section.narrative.sentences.length > 0 && (
        <p className="max-w-3xl text-[15px] leading-7 text-ink">
          <CitedText text={section.narrative.sentences.join(" ")} onCite={onCite} />
        </p>
      )}
      {section.tables.map((t, i) => (
        <Table key={i} table={t} onCite={onCite} />
      ))}
      {section.items.map((item, i) => (
        <div key={i} className="card relative max-w-3xl overflow-hidden py-4 pr-5 pl-6 text-sm">
          <span aria-hidden className="absolute inset-y-0 left-0 w-1 bg-warning" />
          <p className="font-medium text-ink">
            <CitedText text={item.finding ? `[${item.finding}] ${item.title}` : item.title} onCite={onCite} />
          </p>
          {item.flow.sink && (
            <p className="mt-1 text-ink-2">
              Sink:{" "}
              {item.flow.sink_url ? (
                <a className="link font-mono text-xs" href={item.flow.sink_url} target="_blank" rel="noreferrer">
                  {item.flow.sink}
                </a>
              ) : (
                <code className="text-xs">{item.flow.sink}</code>
              )}
            </p>
          )}
          <p className="eyebrow mt-3">Reviewer must check</p>
          <ol className="mt-1 ml-5 list-decimal space-y-1 text-ink-2 marker:text-ink-3">
            {item.instructions.map((text, j) => (
              <li key={j}>{text}</li>
            ))}
          </ol>
        </div>
      ))}
      {section.notes.map((note, i) => (
        <p key={i} className="max-w-3xl border-l-2 border-line-strong pl-3 text-sm leading-relaxed text-ink-2">
          {note}
        </p>
      ))}
      {section.children.map((child) => (
        <Section key={child.id} section={child} level={level + 1} onCite={onCite} />
      ))}
    </section>
  );
}

export function ReportSections({ sections, onCite }: Props) {
  return (
    <div className="space-y-6">
      {sections.map((s) => (
        <Section key={s.id} section={s} level={2} onCite={onCite} />
      ))}
    </div>
  );
}

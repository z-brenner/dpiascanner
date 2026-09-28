import type { ReportSection, ReportTable } from "../api/types";
import { CitedText } from "./Citations";

interface Props {
  sections: ReportSection[];
  onCite: (findingId: string) => void;
}

function Table({ table, onCite }: { table: ReportTable; onCite: (id: string) => void }) {
  if (!table.rows.length) return <p className="text-sm text-stone-500">{table.caption || "Table"}: nothing to list.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="my-2 w-full border-collapse text-left text-sm">
        {table.caption && <caption className="py-1 text-left text-stone-600">{table.caption}</caption>}
        <thead>
          <tr>
            {table.columns.map((c) => (
              <th key={c} className="border border-stone-300 p-1.5 dark:border-stone-700">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className="border border-stone-300 p-1.5 align-top dark:border-stone-700">
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
  return (
    <section id={`sec-${section.id}`} className="space-y-2">
      <Heading className={level === 2 ? "mt-6 border-b border-stone-300 pb-1 text-lg font-semibold dark:border-stone-700" : "mt-4 font-semibold"}>
        {section.title}
      </Heading>
      {section.basis && <p className="text-xs text-stone-500">Basis: {section.basis}</p>}
      {section.intro && <p className="text-sm">{section.intro}</p>}
      {section.narrative && section.narrative.sentences.length > 0 && (
        <p className="text-sm leading-relaxed">
          <CitedText text={section.narrative.sentences.join(" ")} onCite={onCite} />
        </p>
      )}
      {section.tables.map((t, i) => (
        <Table key={i} table={t} onCite={onCite} />
      ))}
      {section.items.map((item, i) => (
        <div key={i} className="rounded border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950">
          <p className="font-medium">
            <CitedText text={item.finding ? `[${item.finding}] ${item.title}` : item.title} onCite={onCite} />
          </p>
          {item.flow.sink && (
            <p>
              Sink:{" "}
              {item.flow.sink_url ? (
                <a className="underline" href={item.flow.sink_url} target="_blank" rel="noreferrer">
                  {item.flow.sink}
                </a>
              ) : (
                <code>{item.flow.sink}</code>
              )}
            </p>
          )}
          <p className="mt-2 font-medium">Reviewer must check:</p>
          <ol className="ml-5 list-decimal">
            {item.instructions.map((text, j) => (
              <li key={j}>{text}</li>
            ))}
          </ol>
        </div>
      ))}
      {section.notes.map((note, i) => (
        <p key={i} className="border-l-2 border-sky-700 pl-2 text-sm text-stone-600 dark:text-stone-400">
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
    <div>
      {sections.map((s) => (
        <Section key={s.id} section={s} level={2} onCite={onCite} />
      ))}
    </div>
  );
}

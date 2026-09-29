const CITATION = /\[(F-\d{4,})\]/g;

/** Text with [F-0042] citations rendered as buttons that open the finding. */
export function CitedText({ text, onCite }: { text: string; onCite: (findingId: string) => void }) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(CITATION)) {
    const index = match.index ?? 0;
    if (index > last) parts.push(text.slice(last, index));
    const id = match[1]!;
    parts.push(
      <button
        key={`${id}-${index}`}
        type="button"
        className="mx-px rounded bg-sunken px-1 py-px font-mono text-[0.8em] text-ink transition-colors hover:bg-accent-soft"
        onClick={() => onCite(id)}
      >
        [{id}]
      </button>,
    );
    last = index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

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
        className="font-mono text-sky-800 underline dark:text-sky-300"
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

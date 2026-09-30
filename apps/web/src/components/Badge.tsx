import type { Severity } from "../api/types";

/** Severity is a status scale: a dot in the status colour, always beside its name. */
const SEVERITY_DOT: Record<Severity, string> = {
  critical: "bg-critical",
  high: "bg-serious",
  medium: "bg-warning",
  low: "bg-ink-3",
  informational: "bg-line-strong",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px] font-medium whitespace-nowrap text-ink capitalize">
      <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${SEVERITY_DOT[severity]}`} />
      {severity}
    </span>
  );
}

type Tone = "neutral" | "good" | "warn" | "bad";

const TONE_DOT: Record<Tone, string | null> = {
  neutral: null,
  good: "bg-good",
  warn: "bg-warning",
  bad: "bg-critical",
};

/** A quiet label chip; the tone shows as a dot, the words carry the meaning. */
export function Pill({ children, tone = "neutral" }: { children: React.ReactNode; tone?: Tone }) {
  const dot = TONE_DOT[tone];
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2 py-0.5 text-xs font-medium whitespace-nowrap text-ink-2">
      {dot && <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${dot}`} />}
      {children}
    </span>
  );
}

const base =
  "inline-flex items-center justify-center gap-1.5 rounded-lg px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45";

export const primaryButton = `${base} bg-ink text-paper hover:bg-ink/85`;
export const button = `${base} border border-line-strong bg-surface text-ink hover:bg-sunken`;
export const ghostButton = `${base} text-ink-2 hover:bg-sunken hover:text-ink`;
export const input =
  "w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-3 transition-colors focus:border-ink focus:outline-none";

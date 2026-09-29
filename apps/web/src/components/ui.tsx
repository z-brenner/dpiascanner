/** Page furniture shared by every screen. */

export function PageHeader({
  eyebrow,
  title,
  children,
  actions,
}: {
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  children?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end gap-x-6 gap-y-3 border-b border-line pb-6">
      <div className="min-w-0 flex-1 space-y-2">
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1 className="display text-3xl leading-tight sm:text-4xl">{title}</h1>
        {children && <div className="max-w-2xl text-[15px] leading-relaxed text-ink-2">{children}</div>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function SectionTitle({ children, aside }: { children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <h2 className="text-sm font-semibold text-ink">{children}</h2>
      {aside && <div className="text-xs text-ink-3">{aside}</div>}
    </div>
  );
}

/** A headline number: label, then the value in the interface face (never the display face). */
export function StatTile({ label, value, note }: { label: string; value: React.ReactNode; note?: string }) {
  return (
    <div className="card px-4 py-3.5">
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold tracking-tight text-ink">{value}</dd>
      {note && <dd className="mt-0.5 text-xs text-ink-3">{note}</dd>}
    </div>
  );
}

/** A probability as a meter: one hue, the track a lighter step of it, the number beside it. */
export function Meter({ value, label }: { value: number; label: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <span className="inline-flex items-center gap-2">
      <span
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={value}
        className="relative inline-block h-1.5 w-16 overflow-hidden rounded-full bg-meter-track"
      >
        <span className="absolute inset-y-0 left-0 rounded-full bg-meter" style={{ width: `${pct}%` }} />
      </span>
      <span className="num text-xs text-ink-2">{value.toFixed(2)}</span>
    </span>
  );
}

export function Notice({
  tone = "neutral",
  title,
  children,
}: {
  tone?: "neutral" | "warn" | "bad";
  title?: React.ReactNode;
  children: React.ReactNode;
}) {
  const bar = { neutral: "bg-line-strong", warn: "bg-warning", bad: "bg-critical" }[tone];
  return (
    <div className="card relative overflow-hidden py-3 pr-4 pl-5 text-sm">
      <span aria-hidden className={`absolute inset-y-0 left-0 w-1 ${bar}`} />
      {title && <p className="font-medium text-ink">{title}</p>}
      <div className="text-ink-2">{children}</div>
    </div>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="flex items-center gap-2 text-sm text-ink-3" role="status">
      <span aria-hidden className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
      {what}…
    </p>
  );
}

export function ErrorText({ children }: { children: React.ReactNode }) {
  return (
    <Notice tone="bad" title="Something went wrong">
      {children}
    </Notice>
  );
}

/** The Katz mark: a cat's eye, which sees in the dark; the pupil is the accent. */
export function Mark({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden fill="none">
      <path
        d="M2.75 12c2.5-4.1 5.6-6.15 9.25-6.15S18.75 7.9 21.25 12c-2.5 4.1-5.6 6.15-9.25 6.15S5.25 16.1 2.75 12Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="M12 7.4c1.25 1.35 1.85 2.9 1.85 4.6s-.6 3.25-1.85 4.6c-1.25-1.35-1.85-2.9-1.85-4.6s.6-3.25 1.85-4.6Z"
        fill="var(--accent)"
      />
    </svg>
  );
}

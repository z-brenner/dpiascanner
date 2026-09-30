import type { Config, Retention } from "./api/types";

/** "3 days" for 72 hours; hours when the period is not a whole number of days. */
export function period(hours: number): string {
  if (hours % 24 === 0) {
    const days = hours / 24;
    return `${days} day${days === 1 ? "" : "s"}`;
  }
  return `${hours} hour${hours === 1 ? "" : "s"}`;
}

/** The deletion period the site may promise, or null: only while sweeps are running. */
export function promisedRetention(config: Config | null): string | null {
  const retention: Retention | undefined = config?.retention;
  return retention && retention.hours > 0 && retention.active ? period(retention.hours) : null;
}

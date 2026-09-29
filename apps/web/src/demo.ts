/**
 * The public demo's settings. The Cloudflare build (`pnpm run build:cf`) reads them from
 * .env.cloudflare and the deploy workflow; a self-hosted build (`pnpm run build`) leaves them
 * unset and shows no demo notice, because the notice's claims (Cloudflare in the path, an
 * operator you did not choose) would be false there.
 */

export const DEFAULT_SOURCE_URL = "https://github.com/z-brenner/dpiascanner";

export interface DemoSettings {
  /** The repository this demo is built from. */
  sourceUrl: string;
  /** How to reach the operator: an email address or a URL. */
  contact: string | null;
  /** Who hosts the API and database, as a processor, for example "Hetzner Online GmbH (Germany)". */
  backendHost: string | null;
  /** How long the host's database backups keep deleted data, in days, when the operator knows. */
  backupDays: number | null;
}

export function sourceUrl(): string {
  return (import.meta.env.VITE_SOURCE_URL as string | undefined) || DEFAULT_SOURCE_URL;
}

export function demoSettings(): DemoSettings | null {
  const env = import.meta.env;
  if (env.VITE_DEMO_NOTICE !== "1") return null;
  return {
    sourceUrl: sourceUrl(),
    contact: (env.VITE_OPERATOR_CONTACT as string | undefined) || null,
    backendHost: (env.VITE_BACKEND_HOST as string | undefined) || null,
    backupDays: positiveInt(env.VITE_BACKUP_DAYS as string | undefined),
  };
}

function positiveInt(value: string | undefined): number | null {
  const n = Number(value);
  return Number.isInteger(n) && n > 0 ? n : null;
}

/** A mailto: link for a bare email address, the value itself for anything else. */
export function contactHref(contact: string): string {
  return /^[^@\s/:]+@[^@\s/]+$/.test(contact) ? `mailto:${contact}` : contact;
}

/** "owner/repo" for a GitHub URL, the URL itself otherwise. */
export function repoLabel(url: string): string {
  const match = /^https:\/\/github\.com\/([^/]+\/[^/]+?)\/?$/.exec(url);
  return match?.[1] ?? url;
}

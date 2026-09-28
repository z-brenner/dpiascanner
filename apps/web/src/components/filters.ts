import type { Finding, Severity } from "../api/types";

export interface FindingFilters {
  category: string; // "" = all
  severities: Severity[]; // empty = all
  status: "all" | "resolved" | "unresolved";
  verification: "all" | "verified" | "inferred";
}

export const EMPTY_FILTERS: FindingFilters = { category: "", severities: [], status: "all", verification: "all" };
export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "informational"];

/** "verified" means dynamic verification observed data reaching the sink; everything else is
 * inferred from the code (including runs where verification did not run). */
export function isVerified(finding: Finding): boolean {
  return finding.verification === "verified";
}

export function filterFindings(findings: Finding[], filters: FindingFilters): Finding[] {
  return findings.filter(
    (f) =>
      (!filters.category || f.category === filters.category) &&
      (filters.severities.length === 0 || filters.severities.includes(f.severity)) &&
      (filters.status === "all" || f.status === filters.status) &&
      (filters.verification === "all" || (filters.verification === "verified") === isVerified(f)),
  );
}

export function categoryLabel(category: string): string {
  return category.replace(/_/g, " ");
}

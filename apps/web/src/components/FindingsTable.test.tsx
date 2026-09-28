import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Finding } from "../api/types";
import { FindingsTable } from "./FindingsTable";
import { EMPTY_FILTERS, filterFindings } from "./filters";

function finding(id: string, overrides: Partial<Finding>): Finding {
  return {
    id,
    key: `K-${id}`,
    category: "personal_data_to_third_party",
    title: "Personal data sent to a third party",
    severity: "medium",
    severity_modifiers: [],
    status: "resolved",
    verification: "not-run",
    data_categories: ["contact"],
    destination_class: "analytics",
    statute_refs: [],
    reachable: true,
    node_ids: [],
    edge_ids: [],
    decision_ids: [],
    evidence: {},
    ...overrides,
  };
}

const FINDINGS: Finding[] = [
  finding("F-0001", { severity: "critical", data_categories: ["precise_location"], verification: "verified" }),
  finding("F-0002", { category: "reversible_obfuscation", title: "Reversible encoding", severity: "critical" }),
  finding("F-0003", { category: "unresolved_flow", title: "Flow that could not be fully resolved", severity: "high", status: "unresolved", verification: "verified" }),
  finding("F-0004", { category: "indefinite_retention", title: "No retention limit", severity: "medium" }),
  finding("F-0005", { category: "unreachable_flow", title: "Unreachable", severity: "informational", verification: "inferred" }),
];

function visibleIds(): string[] {
  const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
  return rows.map((row) => within(row).getAllByRole("cell")[0]!.textContent ?? "");
}

describe("filterFindings", () => {
  it("combines every filter", () => {
    expect(filterFindings(FINDINGS, EMPTY_FILTERS)).toHaveLength(5);
    expect(filterFindings(FINDINGS, { ...EMPTY_FILTERS, severities: ["critical", "high"] }).map((f) => f.id)).toEqual(["F-0001", "F-0002", "F-0003"]);
    expect(filterFindings(FINDINGS, { ...EMPTY_FILTERS, status: "unresolved" }).map((f) => f.id)).toEqual(["F-0003"]);
    expect(filterFindings(FINDINGS, { ...EMPTY_FILTERS, verification: "verified" }).map((f) => f.id)).toEqual(["F-0001", "F-0003"]);
    // "Inferred" covers every finding without dynamic evidence, including runs where verification did not run.
    expect(filterFindings(FINDINGS, { ...EMPTY_FILTERS, verification: "inferred" }).map((f) => f.id)).toEqual(["F-0002", "F-0004", "F-0005"]);
    expect(filterFindings(FINDINGS, { category: "unresolved_flow", severities: ["critical"], status: "all", verification: "all" })).toEqual([]);
  });
});

describe("FindingsTable", () => {
  it("filters by category, severity, status, and evidence", async () => {
    render(<FindingsTable findings={FINDINGS} onSelect={vi.fn()} />);
    expect(screen.getByText("5 of 5 findings")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Category"), "unresolved flow");
    expect(visibleIds()).toEqual(["F-0003"]);
    await userEvent.selectOptions(screen.getByLabelText("Category"), "All categories");

    await userEvent.click(screen.getByRole("checkbox", { name: "critical" }));
    expect(visibleIds()).toEqual(["F-0001", "F-0002"]);
    await userEvent.click(screen.getByRole("checkbox", { name: "high" }));
    expect(screen.getByText("3 of 5 findings")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Status"), "Unresolved");
    expect(visibleIds()).toEqual(["F-0003"]);
    await userEvent.selectOptions(screen.getByLabelText("Status"), "Resolved and unresolved");

    await userEvent.selectOptions(screen.getByLabelText("Evidence"), "Inferred from code");
    expect(visibleIds()).toEqual(["F-0002"]);

    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByText("5 of 5 findings")).toBeInTheDocument();
    expect(screen.getByLabelText("Evidence")).toHaveValue("all");
  });

  it("opens a finding once per click", async () => {
    const onSelect = vi.fn();
    render(<FindingsTable findings={FINDINGS} onSelect={onSelect} />);
    await userEvent.click(screen.getByRole("button", { name: "F-0004" }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "F-0004" }));
  });
});

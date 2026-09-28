import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StageTracker } from "./StageTracker";

describe("StageTracker", () => {
  it("shows every stage with its status", () => {
    render(
      <StageTracker
        stages={[
          { stage: "clone", status: "done", started_at: "2026-01-01T00:00:00Z", finished_at: "2026-01-01T00:00:02Z", info: {} },
          { stage: "parse", status: "running", started_at: "2026-01-01T00:00:02Z", finished_at: null, info: {} },
          { stage: "verify", status: "skipped", started_at: null, finished_at: null, info: {} },
          { stage: "report", status: "pending", started_at: null, finished_at: null, info: {} },
        ]}
      />,
    );
    const list = screen.getByRole("list", { name: "Run stages" });
    expect(list).toHaveTextContent("Clone: done");
    expect(list).toHaveTextContent("2.0 s");
    expect(list).toHaveTextContent("Parse: running");
    expect(list).toHaveTextContent("Verify: skipped");
    expect(list).toHaveTextContent("Report: pending");
  });
});

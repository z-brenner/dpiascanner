import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Resolution } from "../api/types";
import { RepoResolverField } from "./RepoResolverField";

function resolution(overrides: Partial<Resolution>): Resolution {
  return {
    input: "",
    status: "installed",
    scannable: true,
    full_name: "acme/app",
    owner: "acme",
    repo: "app",
    installation_id: 100,
    private: true,
    default_branch: "main",
    reason: null,
    install_url: null,
    ...overrides,
  };
}

describe("RepoResolverField", () => {
  it("resolves a typed URL on submit and offers Run for a scannable repository", async () => {
    const resolve = vi.fn().mockResolvedValue(resolution({ input: "https://github.com/acme/app" }));
    const onRun = vi.fn();
    render(<RepoResolverField resolve={resolve} onRun={onRun} />);
    const field = screen.getByLabelText("Repository URL or owner/repo");
    await userEvent.type(field, "  https://github.com/acme/app  {enter}");
    expect(resolve).toHaveBeenCalledWith("https://github.com/acme/app");
    expect(await screen.findByText("Can be scanned")).toBeInTheDocument();
    expect(screen.getByText("acme/app")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(onRun).toHaveBeenCalledWith(expect.objectContaining({ full_name: "acme/app", scannable: true }));
  });

  it("resolves pasted text immediately", async () => {
    const resolve = vi.fn().mockResolvedValue(resolution({ full_name: "oss/lib", status: "public", reason: "Public repository" }));
    render(<RepoResolverField resolve={resolve} onRun={vi.fn()} />);
    const field = screen.getByLabelText("Repository URL or owner/repo");
    await userEvent.click(field);
    await userEvent.paste("git@github.com:oss/lib.git");
    expect(resolve).toHaveBeenCalledWith("git@github.com:oss/lib.git");
    expect(await screen.findByText("Public: can be scanned")).toBeInTheDocument();
    expect(field).toHaveValue("git@github.com:oss/lib.git");
  });

  it("offers a one-click install link when the app cannot reach the repository", async () => {
    const resolve = vi.fn().mockResolvedValue(
      resolution({
        status: "inaccessible",
        scannable: false,
        full_name: "acme/secret",
        reason: "Private repository that the Katz app is not installed on.",
        install_url: "https://github.com/apps/lantern-dpia/installations/new",
      }),
    );
    render(<RepoResolverField resolve={resolve} onRun={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Repository URL or owner/repo"), "acme/secret{enter}");
    const link = await screen.findByRole("link", { name: "Install the Katz app" });
    expect(link).toHaveAttribute("href", "https://github.com/apps/lantern-dpia/installations/new");
    expect(screen.queryByRole("button", { name: "Run" })).not.toBeInTheDocument();
    expect(screen.getByText(/not installed/)).toBeInTheDocument();
  });

  it("explains invalid input and API errors, and clears a stale result when editing", async () => {
    const resolve = vi
      .fn()
      .mockResolvedValueOnce(resolution({ status: "invalid", scannable: false, full_name: null, reason: "Enter a GitHub URL" }))
      .mockRejectedValueOnce(new Error("session expired"));
    render(<RepoResolverField resolve={resolve} onRun={vi.fn()} />);
    const field = screen.getByLabelText("Repository URL or owner/repo");
    await userEvent.type(field, "gitlab.com/x/y{enter}");
    expect(await screen.findByText("Not a GitHub repository")).toBeInTheDocument();
    await userEvent.type(field, "z");
    expect(screen.queryByText("Not a GitHub repository")).not.toBeInTheDocument();
    await userEvent.type(field, "{enter}");
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("session expired"));
  });

  it("does not call the API for blank input", async () => {
    const resolve = vi.fn();
    render(<RepoResolverField resolve={resolve} onRun={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Check" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Repository URL or owner/repo"), "   {enter}");
    expect(resolve).not.toHaveBeenCalled();
  });
});

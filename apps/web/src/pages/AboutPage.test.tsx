import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { Config } from "../api/types";
import { BeforeYouInstall, DemoBanner } from "../components/DemoNotice";
import { contactHref } from "../demo";
import { AboutPage } from "./AboutPage";

function config(overrides: Partial<Config> = {}): Config {
  return {
    install_url: "https://github.com/apps/lantern-dpia/installations/new",
    app_slug: "lantern-dpia",
    signin_url: null,
    decision_provider: "stub",
    session_ttl_hours: 168,
    public_repos_only: false,
    retention: { hours: 0, active: false, last_sweep_at: null },
    ...overrides,
  };
}

function renderAt(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("demo notice", () => {
  it("is absent from a self-hosted build, which still says reports are not legal advice", async () => {
    vi.spyOn(api, "config").mockResolvedValue(config());
    renderAt(
      <>
        <DemoBanner />
        <BeforeYouInstall />
        <AboutPage />
      </>,
    );
    expect(await screen.findByRole("heading", { name: "About Katz" })).toBeInTheDocument();
    expect(screen.getByText("They are not legal advice.")).toBeInTheDocument();
    expect(screen.queryByRole("note", { name: "Demo notice" })).toBeNull();
    expect(screen.queryByText(/Cloudflare/)).toBeNull();
  });

  it("names Cloudflare as a processor and recommends self-hosting in the demo build", async () => {
    vi.stubEnv("VITE_DEMO_NOTICE", "1");
    vi.stubEnv("VITE_OPERATOR_CONTACT", "lantern-demo@example.org");
    vi.stubEnv("VITE_BACKEND_HOST", "Example Hosting GmbH (Germany)");
    vi.spyOn(api, "config").mockResolvedValue(config());
    renderAt(
      <>
        <DemoBanner />
        <BeforeYouInstall />
      </>,
    );
    const banner = screen.getByRole("note", { name: "Demo notice" });
    expect(banner).toHaveTextContent("Experimental demo, not a product.");
    expect(banner).toHaveTextContent("Cloudflare processes this site's traffic as a data processor.");
    expect(banner).toHaveTextContent("host it yourself");
    expect(screen.getByRole("heading", { name: "Before you install" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "host Katz yourself" })).toHaveAttribute(
      "href",
      "https://github.com/z-brenner/dpiascanner",
    );
  });

  it("describes the processors, retention, and deletion for the running instance", async () => {
    vi.stubEnv("VITE_DEMO_NOTICE", "1");
    vi.stubEnv("VITE_OPERATOR_CONTACT", "lantern-demo@example.org");
    vi.stubEnv("VITE_BACKEND_HOST", "Example Hosting GmbH (Germany)");
    vi.spyOn(api, "config").mockResolvedValue(config({ session_ttl_hours: 48 }));
    renderAt(<AboutPage />);
    expect(await screen.findByText("2 days")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "About this demo" })).toBeInTheDocument();
    expect(screen.getByText("Example Hosting GmbH (Germany)")).toBeInTheDocument();
    expect(screen.getByText(/No AI model receives your code/)).toBeInTheDocument();
    expect(screen.getByText(/It does not delete results already stored/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "lantern-demo@example.org" })).toHaveAttribute(
      "href",
      "mailto:lantern-demo@example.org",
    );
  });

  it("says when classification leaves the server", async () => {
    vi.stubEnv("VITE_DEMO_NOTICE", "1");
    vi.spyOn(api, "config").mockResolvedValue(config({ decision_provider: "jev" }));
    renderAt(<AboutPage />);
    expect(await screen.findByText("TypeSafe")).toBeInTheDocument();
    expect(screen.queryByText(/No AI model receives your code/)).toBeNull();
    // Without a configured contact, deletion requests go through a public issue naming only a login.
    expect(screen.getByRole("link", { name: "the repository" })).toHaveAttribute(
      "href",
      "https://github.com/z-brenner/dpiascanner/issues",
    );
  });
});

describe("contactHref", () => {
  it("links bare addresses with mailto and leaves URLs alone", () => {
    expect(contactHref("a@b.org")).toBe("mailto:a@b.org");
    expect(contactHref("https://example.org/contact")).toBe("https://example.org/contact");
    expect(contactHref("mailto:a@b.org")).toBe("mailto:a@b.org");
  });
});

describe("the demo's promises follow the server", () => {
  it("states public-only and 3-day deletion only while deletion is running", async () => {
    vi.stubEnv("VITE_DEMO_NOTICE", "1");
    vi.stubEnv("VITE_BACKUP_DAYS", "7");
    vi.spyOn(api, "config").mockResolvedValue(
      config({
        public_repos_only: true,
        session_ttl_hours: 72,
        retention: { hours: 72, active: true, last_sweep_at: "2026-09-29T12:00:00+00:00" },
      }),
    );
    renderAt(
      <>
        <DemoBanner />
        <AboutPage />
      </>,
    );
    expect(await screen.findByText("3 days after the scan")).toBeInTheDocument();
    expect(screen.getByText("3 days after you last sign in")).toBeInTheDocument();
    expect(screen.getByText(/This demo scans public repositories only/)).toBeInTheDocument();
    const banner = screen.getByRole("note", { name: "Demo notice" });
    expect(banner).toHaveTextContent("Public repositories only.");
    expect(banner).toHaveTextContent("Scans are deleted after 3 days.");
    // Deletion is from the live database; the host's backups outlive it, and the page says for how long.
    expect(screen.getByText(/backups can still hold it for up to 7 days after that/)).toHaveTextContent(
      "the next sweep deletes them again",
    );
  });

  it("does not put a number on backups the operator has not described", async () => {
    vi.stubEnv("VITE_DEMO_NOTICE", "1");
    vi.spyOn(api, "config").mockResolvedValue(config());
    renderAt(<AboutPage />);
    const note = await screen.findByText(/backups can still hold it for a while after that/);
    expect(note).not.toHaveTextContent("next sweep");
  });

  it("withdraws the promise when deletion has stopped", async () => {
    vi.stubEnv("VITE_DEMO_NOTICE", "1");
    vi.spyOn(api, "config").mockResolvedValue(
      config({ retention: { hours: 72, active: false, last_sweep_at: "2026-09-20T12:00:00+00:00" } }),
    );
    renderAt(
      <>
        <DemoBanner />
        <AboutPage />
      </>,
    );
    expect(await screen.findByText("Automatic deletion is not running right now")).toBeInTheDocument();
    expect(screen.getAllByText("Until you ask for deletion")).toHaveLength(2);
    expect(screen.getByRole("note", { name: "Demo notice" })).not.toHaveTextContent("deleted after");
  });
});


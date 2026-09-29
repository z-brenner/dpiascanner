// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import worker from "./index";
import {
  apiOrigin,
  forwardHeaders,
  isApiPath,
  proxy,
  rewriteLocation,
  upstreamUrl,
  type Env,
} from "./proxy";

const SITE = "https://lantern.example.com";
const API = "https://api.lantern.example.com";

function env(overrides: Partial<Env> = {}): Env {
  const assets = { fetch: vi.fn(async () => new Response("<!doctype html>", { status: 200 })) };
  return { ASSETS: assets as unknown as Fetcher, API_ORIGIN: API, ...overrides };
}

function upstream(response: Response) {
  const mock = vi.fn(async (_url: URL | string, _init?: RequestInit) => response);
  vi.stubGlobal("fetch", mock);
  return mock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("routing", () => {
  it("proxies only /api and paths under it", () => {
    expect(isApiPath("/api")).toBe(true);
    expect(isApiPath("/api/runs")).toBe(true);
    expect(isApiPath("/apis")).toBe(false);
    expect(isApiPath("/runs/api")).toBe(false);
  });

  it("serves everything else from static assets", async () => {
    const e = env();
    const fetchMock = upstream(new Response("{}"));
    const res = await worker.fetch(new Request(`${SITE}/runs/run-1`) as never, e);
    expect(res.status).toBe(200);
    expect(e.ASSETS.fetch).toHaveBeenCalledOnce();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("apiOrigin", () => {
  it("accepts https, and http only to this machine", () => {
    expect(apiOrigin("https://api.example.com")?.origin).toBe("https://api.example.com");
    expect(apiOrigin("http://localhost:8000")?.origin).toBe("http://localhost:8000");
    expect(apiOrigin("http://127.0.0.1:8000")?.origin).toBe("http://127.0.0.1:8000");
    expect(apiOrigin("http://api.example.com")).toBeNull();
    expect(apiOrigin("https://user:pass@api.example.com")).toBeNull();
    expect(apiOrigin("not a url")).toBeNull();
    expect(apiOrigin("")).toBeNull();
    expect(apiOrigin(undefined)).toBeNull();
  });
});

describe("upstreamUrl", () => {
  const origin = new URL(API);

  it("strips the prefix and keeps the query", () => {
    const url = upstreamUrl(new URL(`${SITE}/api/runs/run-1/findings?severity=high&page=2`), origin);
    expect(url.toString()).toBe(`${API}/runs/run-1/findings?severity=high&page=2`);
    expect(upstreamUrl(new URL(`${SITE}/api`), origin).toString()).toBe(`${API}/`);
  });

  it("keeps a base path on the configured origin", () => {
    const url = upstreamUrl(new URL(`${SITE}/api/auth/me`), new URL(`${API}/v1/`));
    expect(url.toString()).toBe(`${API}/v1/auth/me`);
  });

  it("never lets the path choose the host", () => {
    for (const path of ["/api//evil.example/x", "/api/%2F%2Fevil.example", "/api/@evil.example"]) {
      expect(upstreamUrl(new URL(`${SITE}${path}`), origin).host).toBe("api.lantern.example.com");
    }
  });
});

describe("forwardHeaders", () => {
  it("drops hop-by-hop and spoofable headers and sets the forwarding ones", () => {
    const request = new Request(`${SITE}/api/auth/me`, {
      headers: {
        Cookie: "lantern_session=abc",
        "Content-Type": "application/json",
        "X-Hub-Signature-256": "sha256=deadbeef",
        Connection: "keep-alive",
        "X-Forwarded-Host": "evil.example",
        "X-Forwarded-For": "10.0.0.1",
        "CF-Access-Client-Secret": "guessed",
        "CF-Connecting-IP": "203.0.113.7",
      },
    });
    const headers = forwardHeaders(request, env());
    expect(headers.get("cookie")).toBe("lantern_session=abc");
    expect(headers.get("content-type")).toBe("application/json");
    expect(headers.get("x-hub-signature-256")).toBe("sha256=deadbeef");
    expect(headers.get("connection")).toBeNull();
    expect(headers.get("x-forwarded-host")).toBe("lantern.example.com");
    expect(headers.get("x-forwarded-proto")).toBe("https");
    expect(headers.get("x-forwarded-for")).toBe("203.0.113.7");
    expect(headers.get("cf-access-client-secret")).toBeNull();
    expect(headers.get("cf-connecting-ip")).toBeNull();
  });

  it("adds the Access service token when one is configured", () => {
    const headers = forwardHeaders(
      new Request(`${SITE}/api/runs`),
      env({ CF_ACCESS_CLIENT_ID: "id.access", CF_ACCESS_CLIENT_SECRET: "secret" }),
    );
    expect(headers.get("cf-access-client-id")).toBe("id.access");
    expect(headers.get("cf-access-client-secret")).toBe("secret");
  });
});

describe("rewriteLocation", () => {
  const origin = new URL(API);
  const site = new URL(SITE);

  it("sends API redirects back through the proxy", () => {
    expect(rewriteLocation(`${API}/runs/?page=2`, origin, site)).toBe(`${SITE}/api/runs/?page=2`);
    expect(rewriteLocation("/repos/", origin, site)).toBe(`${SITE}/api/repos/`);
    const requested = new URL(`${API}/runs/run-1/findings`);
    expect(rewriteLocation("detail", origin, site, requested)).toBe(`${SITE}/api/runs/run-1/detail`);
  });

  it("leaves other destinations alone", () => {
    const install = "https://github.com/apps/lantern-dpia/installations/new";
    expect(rewriteLocation(install, origin, site)).toBe(install);
  });

  it("does not strip a base path from a sibling path", () => {
    const based = new URL(`${API}/v1`);
    expect(rewriteLocation(`${API}/v10/x`, based, site)).toBe(`${SITE}/api/v10/x`);
    expect(rewriteLocation(`${API}/v1/x`, based, site)).toBe(`${SITE}/api/x`);
  });
});

describe("proxy", () => {
  it("answers 503 when the API origin is missing or not https", async () => {
    for (const API_ORIGIN of [undefined, "http://api.example.com"]) {
      const res = await proxy(new Request(`${SITE}/api/auth/me`), env({ API_ORIGIN }));
      expect(res.status).toBe(503);
      expect(await res.json()).toEqual({ detail: expect.stringContaining("API_ORIGIN") });
    }
  });

  it("forwards the exact body bytes, so webhook signatures still verify", async () => {
    const payload = '{"action":"opened","number":7}\n';
    const fetchMock = upstream(new Response(null, { status: 202 }));
    const res = await proxy(
      new Request(`${SITE}/api/webhooks/github`, {
        method: "POST",
        body: payload,
        headers: { "X-GitHub-Event": "pull_request" },
      }),
      env(),
    );
    expect(res.status).toBe(202);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe(`${API}/webhooks/github`);
    expect(init?.method).toBe("POST");
    expect(init?.redirect).toBe("manual");
    expect(new TextDecoder().decode(init?.body as ArrayBuffer)).toBe(payload);
  });

  it("passes every Set-Cookie through and marks responses uncacheable", async () => {
    const headers = new Headers({ "Content-Type": "application/json" });
    headers.append("Set-Cookie", "lantern_session=abc; HttpOnly; Secure; SameSite=Lax; Path=/");
    headers.append("Set-Cookie", "other=1; Path=/");
    upstream(new Response("{}", { status: 200, headers }));
    const res = await proxy(new Request(`${SITE}/api/auth/github/callback`, { method: "POST", body: "{}" }), env());
    expect(res.headers.getSetCookie()).toHaveLength(2);
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
  });

  it("keeps the API's own caching and rewrites its redirects", async () => {
    upstream(
      new Response(null, {
        status: 307,
        headers: { Location: `${API}/runs/`, "Cache-Control": "private, max-age=60" },
      }),
    );
    const res = await proxy(new Request(`${SITE}/api/runs`), env());
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe(`${SITE}/api/runs/`);
    expect(res.headers.get("cache-control")).toBe("private, max-age=60");
  });

  it("answers 502 when the API is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("connection refused");
      }),
    );
    const res = await proxy(new Request(`${SITE}/api/runs`), env());
    expect(res.status).toBe(502);
  });
});

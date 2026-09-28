import assert from "node:assert/strict";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { after, before, test } from "node:test";

const received: Array<{ method?: string; url?: string }> = [];
const stub = createServer((req, res) => {
  received.push({ method: req.method, url: req.url });
  req.resume();
  req.on("end", () => {
    res.setHeader("content-type", "application/json");
    res.end(req.method === "PUT" ? "" : JSON.stringify({ id: "cus_stub", object: "customer", status: 1 }));
  });
});
stub.listen(0);
const stubBase = `http://127.0.0.1:${(stub.address() as AddressInfo).port}`;
Object.assign(process.env, {
  LANTERN_MEMORY_DB: "1",
  MIXPANEL_API_URL: stubBase,
  SENTRY_DSN: `http://pk@127.0.0.1:${(stub.address() as AddressInfo).port}/1`,
  STRIPE_API_BASE: stubBase,
  WEATHER_API_URL: `${stubBase}/forecast`,
  PARTNER_API_URL: `${stubBase}/contacts`,
  S3_ENDPOINT_URL: stubBase,
  AWS_ACCESS_KEY_ID: "stub",
  AWS_SECRET_ACCESS_KEY: "stub",
});

const { app } = await import("../src/app.js");
const server = app.listen(0);
const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;

before(() => received.splice(0));
after(() => {
  server.close();
  stub.close();
});

async function post(path: string, body?: unknown): Promise<Response> {
  return fetch(base + path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

test("healthz", async () => {
  assert.deepEqual(await (await fetch(`${base}/healthz`)).json(), { status: "ok" });
});

test("signup then duplicate", async () => {
  const body = { email: "a@example.com", fullName: "A", dateOfBirth: "1990-01-02" };
  assert.equal((await post("/users", body)).status, 201);
  assert.equal((await post("/users", body)).status, 409);
});

test("partner sync calls out", async () => {
  assert.equal((await post("/partners/sync", { phone: "+1-202-555-0100" })).status, 202);
  assert.ok(received.some((r) => r.url === "/contacts"));
});

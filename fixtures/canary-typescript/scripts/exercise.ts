// Drive every reachable route once with synthetic canary values.
// Third-party endpoints are configured through environment variables (see lantern.yml).
import type { AddressInfo } from "node:net";
import * as Sentry from "@sentry/node";

process.env.LANTERN_MEMORY_DB ??= "1";
const { app } = await import("../src/app.js");

const CANARY = {
  email: process.env.LANTERN_CANARY_EMAIL ?? "lantern.canary+7f3a@example.com",
  name: process.env.LANTERN_CANARY_NAME ?? "Lantern Canary",
  phone: process.env.LANTERN_CANARY_PHONE ?? "+1-202-555-0147",
  ssn: process.env.LANTERN_CANARY_SSN ?? "987-65-4321",
  dob: process.env.LANTERN_CANARY_DOB ?? "1987-06-05",
  lat: Number(process.env.LANTERN_CANARY_LAT ?? "47.620422"),
  lng: Number(process.env.LANTERN_CANARY_LNG ?? "-122.349358"),
  condition: process.env.LANTERN_CANARY_CONDITION ?? "canary-condition-asthma",
  paymentMethod: process.env.LANTERN_CANARY_PAYMENT_METHOD ?? "pm_lantern_canary",
  refereeEmail: process.env.LANTERN_CANARY_EMAIL_2 ?? "lantern.referee+7f3a@example.com",
};

const server = app.listen(0);
await new Promise((resolve) => server.once("listening", resolve));
const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;

const signup = {
  email: CANARY.email,
  fullName: CANARY.name,
  dateOfBirth: CANARY.dob,
  plan: "pro",
  paymentMethodId: CANARY.paymentMethod,
};
const calls: Array<[string, RequestInit, number]> = [
  ["/users", json(signup), 201],
  ["/users", json(signup), 409],
  ["/identity/verify", json({ ssn: CANARY.ssn }), 202],
  ["/location/forecast", json({ latitude: CANARY.lat, longitude: CANARY.lng }), 200],
  ["/health/intake", form({ userId: "1", condition: CANARY.condition }), 201],
  ["/referrals", json({ referrerId: 1, refereeEmail: CANARY.refereeEmail }), 201],
  ["/partners/sync", json({ phone: CANARY.phone }), 202],
  ["/users/1/export", { method: "POST" }, 202],
];

let failures = 0;
for (const [path, init, expected] of calls) {
  const response = await fetch(base + path, init);
  const ok = response.status === expected;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok" : "UNEXPECTED"} POST ${path} -> ${response.status}`);
}
await Sentry.flush(5000);
server.close();
process.exit(failures ? 1 : 0);

function json(body: unknown): RequestInit {
  return { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) };
}

function form(body: Record<string, string>): RequestInit {
  return { method: "POST", body: new URLSearchParams(body) };
}

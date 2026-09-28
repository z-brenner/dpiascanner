import * as Sentry from "@sentry/node";
import express, { type NextFunction, type Request, type Response } from "express";
import { DuplicateAccountError } from "./errors.js";
import { initErrorReporting } from "./observability.js";
import { healthRouter } from "./routes/health.js";
import { identityRouter } from "./routes/identity.js";
import { locationRouter } from "./routes/location.js";
import { partnersRouter } from "./routes/partners.js";
import { referralsRouter } from "./routes/referrals.js";
import { usersRouter } from "./routes/users.js";

initErrorReporting();

export const app = express();
app.use(express.json());
app.use(express.urlencoded({ extended: false }));

app.get("/healthz", (_req, res) => {
  res.json({ status: "ok" });
});
app.use(usersRouter);
app.use(identityRouter);
app.use(locationRouter);
app.use(healthRouter);
app.use(referralsRouter);
app.use(partnersRouter);

app.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
  if (err instanceof DuplicateAccountError) {
    Sentry.captureException(err);
    res.status(409).json({ detail: "account already exists" });
    return;
  }
  res.status(500).json({ detail: "internal error" });
});

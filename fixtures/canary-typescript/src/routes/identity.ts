import { Router } from "express";
import { logger } from "../logger.js";
import { encryptField } from "../security.js";

export const identityRouter = Router();

identityRouter.post("/identity/verify", (req, res) => {
  const token = encryptField(req.body.ssn);
  logger.info({ documentType: req.body.documentType ?? "ssn", token }, "identity check queued");
  res.status(202).json({ status: "queued" });
});

import { Router } from "express";
import { pushContact } from "../partners.js";

export const partnersRouter = Router();

partnersRouter.post("/partners/sync", async (req, res) => {
  const profile = { phone: req.body.phone, optIn: Boolean(req.body.marketingOptIn) };
  res.status(202).json({ status: await pushContact(profile) });
});

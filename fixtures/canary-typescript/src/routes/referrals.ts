import { Router } from "express";
import { container } from "../container.js";

export const referralsRouter = Router();

referralsRouter.post("/referrals", async (req, res) => {
  const service = container.referralService;
  const id = await service.register(Number(req.body.referrerId), req.body.refereeEmail);
  res.status(201).json({ id });
});

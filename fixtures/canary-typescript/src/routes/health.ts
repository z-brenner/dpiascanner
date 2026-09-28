import { Router } from "express";
import { prisma } from "../db.js";

export const healthRouter = Router();

healthRouter.post("/health/intake", async (req, res) => {
  const record = await prisma.healthRecord.create({
    data: { userId: Number(req.body.userId), condition: req.body.condition },
  });
  res.status(201).json({ id: record.id });
});

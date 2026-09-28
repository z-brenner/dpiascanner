import { Router } from "express";
import { trackSignup } from "../analytics.js";
import { createBillingCustomer } from "../billing.js";
import { prisma } from "../db.js";
import { DuplicateAccountError } from "../errors.js";
import { identifyUser } from "../observability.js";
import { uploadExport } from "../storage.js";

export const usersRouter = Router();

const SIGNUP_METRIC_TTL_MS = 90 * 24 * 60 * 60 * 1000;

function ageOn(dob: Date, today: Date): number {
  const beforeBirthday =
    today.getMonth() < dob.getMonth() ||
    (today.getMonth() === dob.getMonth() && today.getDate() < dob.getDate());
  return today.getFullYear() - dob.getFullYear() - (beforeBirthday ? 1 : 0);
}

usersRouter.post("/users", async (req, res) => {
  const { email, fullName, dateOfBirth, plan = "free", paymentMethodId } = req.body;
  if (await prisma.user.findUnique({ where: { email } })) {
    throw new DuplicateAccountError(`account already exists for ${email}`);
  }

  const user = await prisma.user.create({ data: { email, fullName, plan } });

  const age = ageOn(new Date(dateOfBirth), new Date());
  await prisma.signupMetric.create({
    data: { age, cohort: plan, expiresAt: new Date(Date.now() + SIGNUP_METRIC_TTL_MS) },
  });

  identifyUser(user.id, email);
  await trackSignup(user.id, email, plan);
  if (paymentMethodId) {
    const customerId = await createBillingCustomer({ email, fullName }, paymentMethodId);
    await prisma.user.update({ where: { id: user.id }, data: { stripeCustomerId: customerId } });
  }
  res.status(201).json({ id: user.id, plan: user.plan });
});

usersRouter.post("/users/:userId/export", async (req, res) => {
  const user = await prisma.user.findUnique({ where: { id: Number(req.params.userId) } });
  if (!user) {
    res.status(404).json({ detail: "user not found" });
    return;
  }
  const document = {
    email: user.email,
    fullName: user.fullName,
    plan: user.plan,
    createdAt: user.createdAt.toISOString(),
  };
  const key = await uploadExport(JSON.stringify(document));
  res.status(202).json({ key });
});

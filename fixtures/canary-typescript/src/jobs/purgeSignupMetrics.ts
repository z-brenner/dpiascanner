// Scheduled job, invoked from deploy/crontab.
import { prisma } from "../db.js";

export async function purgeExpiredSignupMetrics(): Promise<number> {
  const { count } = await prisma.signupMetric.deleteMany({
    where: { expiresAt: { lt: new Date() } },
  });
  return count;
}

if (process.argv[1]?.endsWith("purgeSignupMetrics.ts")) {
  console.log(await purgeExpiredSignupMetrics());
}

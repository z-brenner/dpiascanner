import type { PrismaClient } from "./generated/prisma/client.js";
import { config } from "./config.js";

async function createClient(): Promise<PrismaClient> {
  if (process.env.LANTERN_MEMORY_DB === "1") {
    const { createMemoryClient } = await import("./testing/memoryClient.js");
    return createMemoryClient() as unknown as PrismaClient;
  }
  const { PrismaClient } = await import("./generated/prisma/client.js");
  const { PrismaPg } = await import("@prisma/adapter-pg");
  return new PrismaClient({ adapter: new PrismaPg({ connectionString: config.databaseUrl }) });
}

export const prisma = await createClient();

import type { PrismaClient } from "../generated/prisma/client.js";
import type { ContactRecord } from "../helpers/envelope.js";

export class ContactRepository {
  constructor(private readonly db: PrismaClient) {}

  async save(record: ContactRecord): Promise<number> {
    const row = await this.db.referralContact.create({
      data: { email: record.email, referredBy: record.referredBy, source: record.source },
    });
    return row.id;
  }
}

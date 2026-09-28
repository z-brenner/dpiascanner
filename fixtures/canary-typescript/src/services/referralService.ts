import { buildContactRecord } from "../helpers/envelope.js";
import { normalizeEmail } from "../helpers/normalize.js";
import type { ContactRepository } from "../repositories/contactRepository.js";

export class ReferralService {
  constructor(private readonly contacts: ContactRepository) {}

  async register(referrerId: number, refereeEmail: string): Promise<number> {
    const normalized = normalizeEmail(refereeEmail);
    const record = buildContactRecord(normalized, referrerId, "referral");
    return this.contacts.save(record);
  }
}

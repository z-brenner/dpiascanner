import { prisma } from "./db.js";
import { ContactRepository } from "./repositories/contactRepository.js";
import { ReferralService } from "./services/referralService.js";

export const container = {
  referralService: new ReferralService(new ContactRepository(prisma)),
};

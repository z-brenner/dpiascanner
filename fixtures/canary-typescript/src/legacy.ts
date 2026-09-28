// Contact sync for the pre-2023 CRM integration. Superseded by ./partners.ts.
import axios from "axios";
import { prisma } from "./db.js";

const LEGACY_CRM_URL = "https://legacy-crm.partner.example/api/import";

export async function syncAllContactsToLegacyCrm(): Promise<number> {
  let sent = 0;
  for (const user of await prisma.user.findMany()) {
    await axios.post(LEGACY_CRM_URL, { email: user.email, name: user.fullName }, { timeout: 10000 });
    sent += 1;
  }
  return sent;
}

import axios from "axios";
import { config } from "./config.js";
import { PARTNER_CONTACT_FIELD, PARTNER_TIMEOUT_MS } from "./constants.js";

export async function pushContact(profile: Record<string, unknown>): Promise<number> {
  const contactValue = profile[PARTNER_CONTACT_FIELD];
  const response = await axios.post(
    config.partnerApiUrl,
    { contact: contactValue, source: "canary-app" },
    { timeout: PARTNER_TIMEOUT_MS },
  );
  return response.status;
}

import Mixpanel from "mixpanel";
import { config } from "./config.js";

const apiUrl = new URL(config.mixpanelApiUrl);
const mixpanel = Mixpanel.init(config.mixpanelToken, {
  host: apiUrl.host,
  protocol: apiUrl.protocol.replace(":", ""),
  path: apiUrl.pathname.replace(/\/$/, ""),
});

export function trackSignup(userId: number, email: string, plan: string): Promise<void> {
  return new Promise((resolve) => {
    mixpanel.track("Signed Up", { distinct_id: String(userId), email, plan }, () => resolve());
  });
}

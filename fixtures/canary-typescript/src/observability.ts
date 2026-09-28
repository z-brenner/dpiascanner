import * as Sentry from "@sentry/node";
import { config } from "./config.js";

export function initErrorReporting(): void {
  Sentry.init({
    dsn: config.sentryDsn,
    sendDefaultPii: false,
    tracesSampleRate: 0,
    beforeSend(event) {
      if (event.user) {
        delete event.user.email;
        delete event.user.ip_address;
      }
      return event;
    },
  });
}

export function identifyUser(userId: number, email: string): void {
  Sentry.setUser({ id: String(userId), email });
}

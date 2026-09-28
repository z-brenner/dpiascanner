import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

interface FileConfig {
  s3?: { bucket?: string; region?: string };
}

const configFile = join(dirname(fileURLToPath(import.meta.url)), "..", "config", "settings.json");
const fileConfig: FileConfig = existsSync(configFile)
  ? (JSON.parse(readFileSync(configFile, "utf8")) as FileConfig)
  : {};

export const config = {
  port: Number(process.env.PORT ?? 3000),
  databaseUrl: process.env.DATABASE_URL ?? "postgresql://canary:canary@localhost:5432/canary",
  mixpanelToken: process.env.MIXPANEL_TOKEN ?? "canary-project-token",
  mixpanelApiUrl: process.env.MIXPANEL_API_URL ?? "https://api.mixpanel.com",
  sentryDsn: process.env.SENTRY_DSN ?? "https://publickey@o0.ingest.sentry.io/0",
  stripeApiKey: process.env.STRIPE_API_KEY ?? "",
  stripeApiBase: process.env.STRIPE_API_BASE ?? "https://api.stripe.com",
  weatherApiUrl: process.env.WEATHER_API_URL ?? "https://api.open-weather-data.example/v1/forecast",
  partnerApiUrl: process.env.PARTNER_API_URL ?? "https://hooks.partner-crm.example/v2/contacts",
  s3Bucket: process.env.S3_BUCKET ?? fileConfig.s3?.bucket ?? "canary-user-exports",
  s3Region: process.env.S3_REGION ?? fileConfig.s3?.region ?? "eu-west-1",
  s3EndpointUrl: process.env.S3_ENDPOINT_URL,
};

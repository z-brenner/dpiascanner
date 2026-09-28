import pino from "pino";

export const logger = pino({ name: "canary", level: process.env.LOG_LEVEL ?? "info" });

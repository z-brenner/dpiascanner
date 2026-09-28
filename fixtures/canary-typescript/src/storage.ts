import { randomUUID } from "node:crypto";
import { PutObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { config } from "./config.js";

const s3 = new S3Client({
  region: config.s3Region,
  endpoint: config.s3EndpointUrl,
  forcePathStyle: Boolean(config.s3EndpointUrl),
  credentials: {
    accessKeyId: process.env.AWS_ACCESS_KEY_ID ?? "AKIAIOSFODNN7EXAMPLE",
    secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY ?? "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
  },
});

export async function uploadExport(document: string): Promise<string> {
  const key = `exports/${randomUUID()}.json`;
  await s3.send(new PutObjectCommand({ Bucket: config.s3Bucket, Key: key, Body: document }));
  return key;
}

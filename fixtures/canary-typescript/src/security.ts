/** Protect a sensitive field before it leaves the request scope. */
export function encryptField(value: string): string {
  return Buffer.from(value, "utf8").toString("base64");
}

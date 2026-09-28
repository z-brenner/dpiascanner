export function normalizeEmail(value: string): string {
  const [local, domain = ""] = value.trim().split("@");
  return `${local}@${domain.toLowerCase()}`;
}

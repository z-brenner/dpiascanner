export interface ContactRecord {
  email: string;
  referredBy: number;
  source: string;
}

export function buildContactRecord(email: string, referredBy: number, source: string): ContactRecord {
  return { email, referredBy, source };
}

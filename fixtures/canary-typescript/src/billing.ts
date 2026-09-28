import Stripe from "stripe";
import { config } from "./config.js";

const apiBase = new URL(config.stripeApiBase);
const stripe = new Stripe(config.stripeApiKey || "sk_test_placeholder", {
  host: apiBase.hostname,
  port: apiBase.port || undefined,
  protocol: apiBase.protocol === "http:" ? "http" : "https",
  maxNetworkRetries: 0,
});

export interface BillingProfile {
  email: string;
  fullName: string;
}

export async function createBillingCustomer(
  profile: BillingProfile,
  paymentMethodId: string,
): Promise<string> {
  const customer = await stripe.customers.create({
    name: profile.fullName,
    email: profile.email,
    payment_method: paymentMethodId,
    invoice_settings: { default_payment_method: paymentMethodId },
  });
  return customer.id;
}

import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./api";

// The CRM subscription (roadmap 3.8): the admin keeps the tariffs, gives a specialist a
// tariff and marks the payments received outside the system; the specialist sees their
// own subscription, read-only.

export const CURRENCIES = ["BYN", "PLN", "USD", "EUR"] as const;
export type Currency = (typeof CURRENCIES)[number];

export type SubscriptionStatus = "none" | "trial" | "paid" | "due_soon" | "overdue";

export type SubscriptionState = {
  status: SubscriptionStatus;
  next_due_on: string | null;
  overdue_days: number;
};

export type TariffBrief = {
  id: string;
  name: string;
  price: string;
  currency: Currency;
  is_active: boolean;
};

export type Tariff = TariffBrief & { specialists: number };

export type SubscriptionPayment = {
  id: string;
  paid_on: string;
  months: number;
  amount: string;
  currency: Currency;
  note: string | null;
};

export type Subscription = SubscriptionState & {
  tariff: TariffBrief | null;
  starts_on: string | null;
  trial_until: string | null;
  // The newest first.
  payments: SubscriptionPayment[];
};

export type SubscriptionRow = SubscriptionState & {
  tenant_id: string;
  name: string;
  email: string;
  is_active: boolean;
  tariff: string | null;
  last_paid_on: string | null;
};

export const TARIFFS_KEY = ["admin", "tariffs"] as const;
// Every admin list of subscriptions sits under this key, so one invalidation refreshes all.
export const SUBSCRIPTIONS_KEY = ["admin", "subscriptions"] as const;
export const OWN_SUBSCRIPTION_KEY = ["subscription"] as const;

export function useTariffs() {
  return useQuery({ queryKey: TARIFFS_KEY, queryFn: () => apiGet<Tariff[]>("/admin/tariffs") });
}

export function useSubscriptions() {
  return useQuery({
    queryKey: SUBSCRIPTIONS_KEY,
    queryFn: () => apiGet<SubscriptionRow[]>("/admin/subscriptions"),
  });
}

/** Overdue and coming-up payments of the specialists who are not blocked. */
export function useDueSubscriptions() {
  return useQuery({
    queryKey: [...SUBSCRIPTIONS_KEY, "due"],
    queryFn: () => apiGet<SubscriptionRow[]>("/admin/subscriptions/due"),
  });
}

export function useSubscriptionOf(tenantId: string) {
  return useQuery({
    queryKey: [...SUBSCRIPTIONS_KEY, tenantId],
    queryFn: () => apiGet<Subscription>(`/admin/specialists/${tenantId}/subscription`),
  });
}

export function useOwnSubscription(enabled: boolean) {
  return useQuery({
    queryKey: OWN_SUBSCRIPTION_KEY,
    queryFn: () => apiGet<Subscription>("/subscription"),
    enabled,
  });
}

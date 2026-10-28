import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { apiGet } from "./api";

// Countries and visa center operators (shared: the admin edits them) and the workspace's
// directions — appointment rules with their cities, set up by the specialist (backlog 1.20,
// backend/app/api/reference.py).

export const BOOKING_TYPES = [
  "waiting_list",
  "windowed_application",
  "direct_slot",
  "embassy",
] as const;
export type BookingType = (typeof BOOKING_TYPES)[number];

export const IDENTIFIER_TYPES = ["vac_id", "application_number", "form_number", "username"] as const;
export type IdentifierType = (typeof IDENTIFIER_TYPES)[number];

export type Country = {
  id: string;
  code: string;
  name_ru: string;
  name_en: string;
  consultation_minutes: number | null;
  passport_max_age_years: number | null;
  passport_valid_after_months: number | null;
  // The day it joined the Schengen area: its days count towards 90/180 from then on.
  schengen_since: string | null;
  is_active: boolean;
};

export type Operator = { id: string; code: string; name: string; is_active: boolean };

// city_ru is the city as typed; the English name is optional.
export type VisaCenter = { id: string; city_ru: string; city_en: string | null; is_active: boolean };

export type Rule = {
  id: string;
  destination_id: string;
  submission_id: string;
  booking_type: BookingType;
  window_days: number | null;
  vac_operator_id: string | null;
  represented_by_id: string | null;
  identifier_type: IdentifierType | null;
  appeals_accepted: boolean | null;
  appeal_deadline_days: number | null;
  notes: string | null;
  is_active: boolean;
  updated_at: string;
  visa_centers: VisaCenter[];
};

/** Where clients of this platform submit applications by default. */
export const DEFAULT_SUBMISSION_CODE = "BY";

/** Admin panel: every country and operator, switched-off ones included. Workspace: active
 * ones only. */
export function referenceBase(admin: boolean) {
  return admin ? "/admin/reference" : "/reference";
}

export function useCountries(admin = false) {
  return useQuery({
    queryKey: ["reference", admin ? "admin" : "workspace", "countries"],
    queryFn: () => apiGet<Country[]>(`${referenceBase(admin)}/countries`),
  });
}

export function useOperators(admin = false) {
  return useQuery({
    queryKey: ["reference", admin ? "admin" : "workspace", "operators"],
    queryFn: () => apiGet<Operator[]>(`${referenceBase(admin)}/operators`),
  });
}

export const RULES_KEY = ["reference", "workspace", "rules"] as const;

/** The workspace's directions that are on, with their cities that are on: for forms. */
export function useRules(submissionId?: string) {
  return useQuery({
    queryKey: [...RULES_KEY, submissionId ?? "all"],
    queryFn: () =>
      apiGet<Rule[]>(`/reference/rules${submissionId ? `?submission=${submissionId}` : ""}`),
  });
}

/** Every direction and city, switched-off ones included: for the builder. */
export function useAllRules() {
  return useQuery({
    queryKey: [...RULES_KEY, "builder"],
    queryFn: () => apiGet<Rule[]>("/reference/rules?include_inactive=true"),
  });
}

/** Names follow the interface language. */
export function useLocalName() {
  const { i18n } = useTranslation();
  const en = i18n.language === "en";
  return useMemo(
    () => ({
      country: (c: Country | undefined) => (c ? (en ? c.name_en : c.name_ru) : ""),
      city: (v: VisaCenter) => (en ? (v.city_en ?? v.city_ru) : v.city_ru),
    }),
    [en],
  );
}

export function byId<T extends { id: string }>(items: T[] | undefined): Map<string, T> {
  return new Map((items ?? []).map((item) => [item.id, item]));
}

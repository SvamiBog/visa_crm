import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { apiGet } from "./api";

// Shared reference: countries, visa center operators and appointment rules
// (backend/app/api/reference.py). The admin edits it; workspaces read it.

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
  is_active: boolean;
};

export type Operator = { id: string; code: string; name: string; is_active: boolean };

export type VisaCenter = { id: string; city_ru: string; city_en: string; is_active: boolean };

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
  is_verified: boolean;
  notes: string | null;
  is_active: boolean;
  updated_at: string;
  visa_centers: VisaCenter[];
};

/** Where clients of this platform submit applications by default. */
export const DEFAULT_SUBMISSION_CODE = "BY";

/** Admin panel: every entry, switched-off ones included. Workspace: active ones only. */
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

export function useRules(admin: boolean, submissionId?: string) {
  return useQuery({
    queryKey: ["reference", admin ? "admin" : "workspace", "rules", submissionId ?? "all"],
    queryFn: () =>
      apiGet<Rule[]>(
        `${referenceBase(admin)}/rules${submissionId ? `?submission=${submissionId}` : ""}`,
      ),
  });
}

/** Names follow the interface language. */
export function useLocalName() {
  const { i18n } = useTranslation();
  const en = i18n.language === "en";
  return useMemo(
    () => ({
      country: (c: Country | undefined) => (c ? (en ? c.name_en : c.name_ru) : ""),
      city: (v: VisaCenter) => (en ? v.city_en : v.city_ru),
    }),
    [en],
  );
}

export function byId<T extends { id: string }>(items: T[] | undefined): Map<string, T> {
  return new Map((items ?? []).map((item) => [item.id, item]));
}

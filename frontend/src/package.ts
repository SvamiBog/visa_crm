import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPut } from "./api";
import type { VisaCase } from "./contacts";

// The package check of a case (backlog 2.3, backend/app/api/package.py): the case's
// document checklist with what the specialist marked and each document's status at the
// submission date, or at today until the case has one.

export const PACKAGE_STATES = ["present", "missing", "not_needed"] as const;
export type PackageState = (typeof PACKAGE_STATES)[number];
export type ItemStatus = "ok" | "missing" | "no_date" | "expired" | "not_needed";

export type PackageItem = {
  item_id: string;
  own: boolean;
  name: string;
  note: string | null;
  // False: «по ситуации» — shown, but does not hold the package back.
  required: boolean;
  max_age_days: number | null;
  state: PackageState | null;
  document_date: string | null;
  status: ItemStatus;
};

export type Package = {
  // The day freshness is counted to: the submission date, or today until there is one.
  on: string;
  on_submission: boolean;
  ready: boolean;
  // Required documents still to collect.
  missing: number;
  items: PackageItem[];
};

export type Mark = { state: PackageState; document_date: string | null };

const packageKey = (caseId: string) => ["cases", caseId, "package"] as const;

/** The package changes with what decides the checklist and the day it is counted to. */
export function usePackage(c: VisaCase) {
  return useQuery({
    queryKey: [
      ...packageKey(c.id),
      c.destination_id,
      c.submission_country_id,
      c.purpose_id,
      c.submission_on,
    ],
    queryFn: () => apiGet<Package>(`/cases/${c.id}/package`),
  });
}

export function useMarkItem(caseId: string, itemId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (mark: Mark) => apiPut<PackageItem>(`/cases/${caseId}/package/${itemId}`, mark),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: packageKey(caseId) });
      // «Требует внимания» and the nearest submissions count the same documents.
      void queryClient.invalidateQueries({ queryKey: ["reminders"] });
      void queryClient.invalidateQueries({ queryKey: ["home"] });
    },
  });
}

import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./api";

// Document checklists (backlog 2.2, backend/app/api/checklists.py): the shared list the
// admin keeps for each country and a specialist's own items on top of it.

/** The platform's purpose kinds (backend PurposeKind): shared lists go by them. */
export const PURPOSE_KINDS = [
  "tourism",
  "visit",
  "business",
  "study",
  "work",
  "culture",
  "sports",
  "official",
  "medical",
  "transit",
  "other",
] as const;
export type PurposeKind = (typeof PURPOSE_KINDS)[number];

export type ChecklistItem = {
  id: string;
  country_id: string;
  // Only when applying in this country; null: anywhere.
  submission_id: string | null;
  // Only for these purpose kinds; empty: for any purpose.
  purposes: PurposeKind[];
  name: string;
  note: string | null;
  // False: depends on the applicant.
  required: boolean;
  // Issued at most that many days before the submission day.
  max_age_days: number | null;
  sort_order: number;
  is_active: boolean;
};

export type Checklist = { shared: ChecklistItem[]; own: ChecklistItem[] };
export type ItemCount = { country_id: string; count: number };

/** Admin panel: the shared list. Workspace: the shared list and the own items. */
export function checklistBase(admin: boolean) {
  return admin ? "/admin/checklists" : "/checklists";
}

export const CHECKLISTS_KEY = ["checklists"] as const;

export function useChecklistCounts(admin: boolean) {
  return useQuery({
    queryKey: [...CHECKLISTS_KEY, admin ? "admin" : "workspace", "counts"],
    queryFn: () => apiGet<ItemCount[]>(checklistBase(admin)),
  });
}

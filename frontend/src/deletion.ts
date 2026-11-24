import type { TFunction } from "i18next";

// The question before a case or a client's card is deleted names everything that goes
// with it (docs/decisions.md → «Удаление кейса и карточки со всем содержимым»).

/** What cases hold, as the API counts it before a deletion (CaseContents in
 * backend/app/api/cases.py); for a card, summed over all its cases. */
export type CaseContents = {
  payments: number;
  /** In the workspace's base currency. */
  payments_total: string;
  expenses: number;
  /** Talons a registrar charged: his balance changes. */
  talons: number;
  /** Partner payouts marked as paid. */
  partner_payouts: number;
  attempts: number;
  queue_entries: number;
  package_items: number;
  bookings: number;
  outcomes: number;
  employees: number;
  /** Cases with a price or the partner's terms and no money moved. */
  terms: number;
};

const COUNTED = [
  "expenses",
  "talons",
  "partner_payouts",
  "attempts",
  "queue_entries",
  "package_items",
  "bookings",
  "outcomes",
  "employees",
  "terms",
] as const;

/** One line for each kind of thing the cases hold, money first; what they lack is left
 * out. `total` formats the payments' sum with the base currency. */
export function contentLines(
  what: CaseContents,
  t: TFunction,
  total: (amount: string) => string,
): string[] {
  return [
    ...(what.payments > 0
      ? [t("cases.deletedWith.payments", { n: what.payments, total: total(what.payments_total) })]
      : []),
    ...COUNTED.filter((key) => what[key] > 0).map((key) =>
      t(`cases.deletedWith.${key}`, { n: what[key] }),
    ),
  ];
}

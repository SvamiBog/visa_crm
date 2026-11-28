/** The tabs of the client's card (backlog 1.15.17): the tab is in the address, so a reload
 * or «back» from another page comes back to it, and a link from another page opens the
 * tab its subject is on. Kept apart from ContactPage.tsx so that the pages linking to a
 * card (the cases block among them) do not import the card itself. */

export const CARD_TABS = ["client", "cases", "documents", "trips", "vault"] as const;
export type CardTab = (typeof CARD_TABS)[number];

/** The query parameter the tab is kept in: `?tab=cases`. */
export const TAB_PARAM = "tab";

/** The address of a client's card on one tab; «Клиент» needs no parameter. */
export function cardPath(contactId: string, tab: CardTab = "client"): string {
  const path = `/app/clients/${contactId}`;
  return tab === "client" ? path : `${path}?${TAB_PARAM}=${tab}`;
}

/** The tab named in the address, or null when it names none of them. */
export function tabOf(search: URLSearchParams): CardTab | null {
  const value = search.get(TAB_PARAM);
  return CARD_TABS.find((tab) => tab === value) ?? null;
}

/** History state of a card opened to show one case: the card opens «Кейсы» with that case
 * unfolded, in view and with the focus on its heading — once, like `enterResult`. */
export function showCase(caseId: string): { showCase: string } {
  return { showCase: caseId };
}

export function caseToShow(state: unknown): string | null {
  if (typeof state !== "object" || state === null || !("showCase" in state)) return null;
  return typeof state.showCase === "string" ? state.showCase : null;
}

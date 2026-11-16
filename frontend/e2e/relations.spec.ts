import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// «Семья и связи»: when two clients are linked, who is who is asked under each person's
// name («Иванова Анна — мама», «Иванова Валерия — дочь»), not with «Кем он приходится
// этому клиенту», which the specialist could not follow. Each word lands in the right
// card. Both languages.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Item = { id: string; full_name: string };
type Card = { relations: { related_id: string; label: string | null }[] };

/** Two clients with names nobody else has, not linked yet. The tests run in parallel, so
 * each language takes its own pair (`which`) out of pairs that share nobody. */
async function unlinkedPair(page: Page, which: number): Promise<[Item, Item]> {
  const found = await page.request.post("/api/contacts/search", { data: {} });
  expect(found.ok()).toBe(true);
  const items = (await found.json()).items as Item[];
  const unique = (item: Item) => items.filter((i) => i.full_name === item.full_name).length === 1;
  let seen = 0;
  for (let i = 0; i + 1 < items.length; i += 2) {
    const [a, b] = [items[i], items[i + 1]];
    if (!unique(a) || !unique(b)) continue;
    const card = (await (await page.request.get(`/api/contacts/${a.id}`)).json()) as Card;
    if (card.relations.some((r) => r.related_id === b.id)) continue;
    if (seen++ === which) return [a, b];
  }
  throw new Error("The demo has too few clients to link");
}

for (const lang of ["ru", "en"] as const) {
  test(`linking two clients asks who is who under their names (${lang})`, async ({ page }) => {
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };
    expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
    await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
    const login = await page.request.post("/api/auth/login", {
      data: { email: EMAIL, password: PASSWORD },
    });
    expect(login.ok(), await login.text()).toBe(true);
    const [mother, daughter] = await unlinkedPair(page, lang === "ru" ? 0 : 1);

    try {
      await page.goto(`/app/clients/${mother.id}`);
      const relations = page
        .locator("section.card")
        .filter({ has: page.getByRole("heading", { name: t("contacts.relations"), exact: true }) });
      await relations.getByRole("button", { name: t("contacts.addRelation"), exact: true }).click();
      await relations
        .getByLabel(t("contacts.findContact"), { exact: true })
        .fill(daughter.full_name);
      await relations
        .getByRole("listitem")
        .filter({ hasText: daughter.full_name })
        .getByRole("button", { name: t("contacts.pick"), exact: true })
        .click();

      await expect(relations.getByText(t("contacts.whoIsWho"), { exact: true })).toBeVisible();
      await relations.getByLabel(mother.full_name, { exact: true }).fill(t("contacts.kin.mother"));
      await relations
        .getByLabel(daughter.full_name, { exact: true })
        .fill(t("contacts.kin.daughter"));
      await relations.getByRole("button", { name: t("contacts.link"), exact: true }).click();

      // The mother's card shows the daughter as «дочь», the daughter's card the mother as «мама».
      await expect(
        relations.getByRole("listitem").filter({ hasText: daughter.full_name }),
      ).toContainText(t("contacts.kin.daughter"));
      const other = (await (
        await page.request.get(`/api/contacts/${daughter.id}`)
      ).json()) as Card;
      expect(other.relations.find((r) => r.related_id === mother.id)?.label).toBe(
        t("contacts.kin.mother"),
      );
      // A link is not a joint filing: the card says where a family is filed together.
      await expect(relations.getByText(t("contacts.familyCaseHint"), { exact: true })).toBeVisible();
    } finally {
      await page.request.delete(`/api/contacts/${mother.id}/relations/${daughter.id}`);
    }
  });
}

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// Deleting a partner from the Lists: one no client card names goes at once; one a card
// names cannot be deleted, and she is offered to switch it off instead.
// Both languages; each run takes its own partner names, as the tests run in parallel.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Partner = { id: string; name: string };
type Contact = { id: string; full_name: string };

const UNUSED: Record<Lang, string> = { ru: "E2E партнёр без клиентов", en: "E2E idle partner" };
const USED: Record<Lang, string> = { ru: "E2E партнёр с клиентом", en: "E2E busy partner" };
// The card that names the used partner; made and removed by the test.
const CLIENT: Record<Lang, string> = { ru: "E2EКлиентПартнёра", en: "E2EPartnerClient" };

type Names = { unused: string; used: string; client: string };

/** This test's names. The worker's slot keeps copies of the test running at once apart. */
function ownNames(lang: Lang): Names {
  const slot = ` [${test.info().parallelIndex}]`;
  return { unused: UNUSED[lang] + slot, used: USED[lang] + slot, client: CLIENT[lang] + slot };
}

/** The card and the partners left by an earlier run on the same stand go first. */
async function cleanUp(request: APIRequestContext, lang: Lang, names: Names): Promise<void> {
  const found = await request.post("/api/contacts/search", { data: { query: CLIENT[lang] } });
  expect(found.ok()).toBe(true);
  for (const card of (await found.json()).items as Contact[]) {
    if (card.full_name === names.client) {
      expect((await request.delete(`/api/contacts/${card.id}`)).ok()).toBe(true);
    }
  }
  const ours = new Set([names.unused, names.used]);
  const partners = (await (await request.get("/api/dictionaries/partners")).json()) as Partner[];
  for (const partner of partners.filter((p) => ours.has(p.name))) {
    const deleted = await request.delete(`/api/dictionaries/partners/${partner.id}`);
    expect(deleted.ok(), await deleted.text()).toBe(true);
  }
}

async function addPartner(request: APIRequestContext, name: string): Promise<Partner> {
  const created = await request.post("/api/dictionaries/partners", { data: { name } });
  expect(created.ok(), await created.text()).toBe(true);
  return (await created.json()) as Partner;
}

/** The site asks exactly this; its button of that name answers, and the window closes. */
async function answer(page: Page, question: string, button: string): Promise<void> {
  const ask = page.getByRole("dialog", { name: question, exact: true });
  await ask.getByRole("button", { name: button, exact: true }).click();
  await expect(ask).toBeHidden();
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`partners in ${lang}`, () => {
    const t = (key: string, values: Record<string, string | number> = {}) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return Object.entries(values).reduce(
        (text, [name, value]) => text.replace(`{{${name}}}`, String(value)),
        LOCALES[lang][key],
      );
    };

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
      await cleanUp(page.request, lang, ownNames(lang));
    });

    test.afterEach(async ({ page }) => {
      await cleanUp(page.request, lang, ownNames(lang));
    });

    test("an unused partner is deleted, a used one is switched off", async ({ page }) => {
      const { unused, used, client } = ownNames(lang);
      await addPartner(page.request, unused);
      const busy = await addPartner(page.request, used);
      const card = await page.request.post("/api/contacts", {
        data: { first_name: client, partner_id: busy.id },
      });
      expect(card.ok(), await card.text()).toBe(true);

      await page.goto("/app/dictionaries");
      await page.getByRole("tab", { name: t("dict.tab.partners") }).click();
      const deleteButton = (name: string) =>
        page.getByRole("button", { name: t("dict.deletePartnerFor", { name }) });

      await deleteButton(unused).click();
      await answer(page, t("dict.confirmDeletePartner", { name: unused }), t("confirm.delete"));
      await expect(page.getByRole("row").filter({ hasText: unused })).toHaveCount(0);

      // A card names this one: after «Delete?» comes the offer to switch it off.
      await deleteButton(used).click();
      await answer(page, t("dict.confirmDeletePartner", { name: used }), t("confirm.delete"));
      await answer(page, t("dict.partnerInUseSwitchOff"), t("confirm.switchOff"));
      const row = page.getByRole("row").filter({ hasText: used });
      await expect(row).toContainText(t("reference.off"));
      // «In use» led to the offer, so no error is shown.
      await expect(page.locator(".alert-error")).toHaveCount(0);
    });
  });
}

import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// «Удалить карточку» asks first and says what will happen to this very card (pilot remark
// 1.15.13): what goes with it, counted, that its entries in the audit log stay, that it
// cannot be restored. The red «Удалить карточку» and «Отмена»; «Отмена» and Escape keep
// the card and send nothing. A card that has got client history meanwhile is refused
// before the question. Both languages, on a phone.
//
// Nothing here is deleted or changed: other specs open the newest or the oldest card
// while this one runs, so it asks on a demo card and refuses. The deletion itself, after
// the red button, is in confirm-undo.spec.ts on a card that test makes for itself, and
// what the API deletes with a card in backend/tests/test_contacts.py.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";
// The demo's own clients (Telegram demo_<n>_<m>), not cards other specs make and delete.
const DEMO = "demo_";

type Deletion = {
  has_history: boolean;
  consultations: number;
  notes: number;
  passport: boolean;
  questionnaire: boolean;
};

/** The newest demo card that can be deleted and has something to go with it: the oldest
 * demo cards get cases for a while in case-delete.spec.ts. */
async function deletableCard(page: Page): Promise<{ id: string; name: string; what: Deletion }> {
  const found = await page.request.post("/api/contacts/search", { data: { query: DEMO } });
  expect(found.ok(), await found.text()).toBe(true);
  for (const { id, full_name } of (await found.json()).items as {
    id: string;
    full_name: string;
  }[]) {
    const response = await page.request.get(`/api/contacts/${id}/deletion`);
    expect(response.ok(), await response.text()).toBe(true);
    const what = (await response.json()) as Deletion;
    if (!what.has_history && what.consultations > 0) return { id, name: full_name, what };
  }
  throw new Error("The demo has no card without history that has a consultation");
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`deleting a client's card in ${lang}`, () => {
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
      await page.setViewportSize({ width: 390, height: 900 });
    });

    test("the question says what goes with the card and «Cancel» keeps it", async ({
      page,
    }) => {
      const { id, name, what } = await deletableCard(page);
      const items = [
        ...(["consultations", "notes"] as const)
          .filter((key) => what[key] > 0)
          .map((key) => t(`contacts.deletedWith.${key}`, { n: what[key] })),
        ...(["passport", "questionnaire"] as const)
          .filter((key) => what[key])
          .map((key) => t(`contacts.deletedWith.${key}`)),
      ];
      const question = t("contacts.confirmDeleteCardWith", { name, items: items.join(", ") });
      const sent: string[] = [];
      page.on("request", (request) => {
        if (request.method() === "DELETE") sent.push(new URL(request.url()).pathname);
      });

      await page.goto(`/app/clients/${id}`);
      const button = page
        .locator(".page-header")
        .getByRole("button", { name: t("contacts.deleteCard"), exact: true });
      const ask = page.getByRole("dialog");
      await button.click();
      await expect(ask.locator(".confirm-text")).toHaveText(question);
      // Not the generic question: this card's consultations are named and counted.
      await expect(ask).toContainText(
        t("contacts.deletedWith.consultations", { n: what.consultations }),
      );
      const remove = ask.getByRole("button", { name: t("contacts.deleteCard"), exact: true });
      await expect(remove).toHaveClass(/btn-danger/);
      const cancel = ask.getByRole("button", { name: t("common.cancel"), exact: true });
      await expect(cancel, "a stray Enter deletes nothing").toBeFocused();

      await cancel.click();
      await expect(ask).toHaveCount(0);
      await expect(button, "the focus goes back to the button").toBeFocused();
      await button.click();
      await expect(ask).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(ask).toHaveCount(0);

      expect(sent, "nothing is deleted without the red button").toEqual([]);
      const card = await page.request.get(`/api/contacts/${id}`);
      expect(card.status()).toBe(200);
      expect(((await card.json()) as { can_delete: boolean }).can_delete).toBe(true);
    });

    test("a card that has got client history meanwhile is refused without the question", async ({
      page,
    }) => {
      const { id } = await deletableCard(page);
      // As if a case had been opened for the client in another tab since the card loaded.
      await page.route(`**/api/contacts/${id}/deletion`, async (route) => {
        const response = await route.fetch();
        await route.fulfill({ response, json: { ...(await response.json()), has_history: true } });
      });
      await page.goto(`/app/clients/${id}`);
      await page
        .locator(".page-header")
        .getByRole("button", { name: t("contacts.deleteCard"), exact: true })
        .click();
      await expect(
        page.getByRole("alert").filter({ hasText: t("errors.contact_has_history") }),
      ).toBeVisible();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      expect((await page.request.get(`/api/contacts/${id}`)).status()).toBe(200);
    });
  });
}

import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// «Удалить карточку» asks first and lists what goes with this very card (pilot remark
// 1.15.13; owner's decision 2026-11-24 → «Удаление кейса и карточки со всем содержимым»):
// a card with client history goes too, so the list names its cases and their money as
// well as its own consultations, notes and the rest, counted; under it, that the card's
// entries in the audit log stay and that it cannot be restored. The red «Удалить
// карточку» and «Отмена»; «Отмена» and Escape keep the card and send nothing. Both
// languages, on a phone.
//
// Nothing here is deleted or changed: other specs open the newest or the oldest card
// while this one runs, so it asks on demo cards and refuses. The deletion itself, after
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
  cases: number[];
  payments: number;
  payments_total: string;
  consultations: number;
  paid_consultations: number;
};

type Found = { id: string; name: string; what: Deletion };

/** The newest demo card the test wants: the oldest demo cards get cases for a while in
 * case-delete.spec.ts, which would change what their question lists. */
async function demoCard(page: Page, wanted: (what: Deletion) => boolean): Promise<Found> {
  const found = await page.request.post("/api/contacts/search", { data: { query: DEMO } });
  expect(found.ok(), await found.text()).toBe(true);
  for (const { id, full_name } of (await found.json()).items as {
    id: string;
    full_name: string;
  }[]) {
    const response = await page.request.get(`/api/contacts/${id}/deletion`);
    expect(response.ok(), await response.text()).toBe(true);
    const what = (await response.json()) as Deletion;
    if (wanted(what)) return { id, name: full_name, what };
  }
  throw new Error("The demo has no such card");
}

/** The payments' sum as the question writes it (useMoney in src/pages/clients/Finance.tsx). */
function moneyText(lang: Lang, amount: string, currency: string): string {
  const number = Number(amount);
  const text = new Intl.NumberFormat(lang, {
    minimumFractionDigits: Number.isInteger(number) ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(number);
  return `${text} ${currency}`;
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

    test("the question lists what goes with the card and «Cancel» keeps it", async ({
      page,
    }) => {
      const { id, name, what } = await demoCard(page, (w) => w.consultations > 0);
      const consultations =
        what.paid_consultations > 0
          ? t("contacts.deletedWith.consultationsPaid", {
              n: what.consultations,
              paid: what.paid_consultations,
            })
          : t("contacts.deletedWith.consultations", { n: what.consultations });
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
      await expect(ask.locator(".confirm-text")).toHaveText(
        t("contacts.confirmDeleteCardWith", { name }),
      );
      // Not the generic question: this card's consultations are named and counted.
      await expect(ask.locator(".confirm-list li").filter({ hasText: consultations })).toHaveCount(
        1,
      );
      await expect(ask.locator(".confirm-note")).toHaveText(t("contacts.deleteCardNote"));
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

    test("a card with cases names them and the money they hold", async ({ page }) => {
      const { id, what } = await demoCard(page, (w) => w.cases.length > 0 && w.payments > 0);
      const { base_currency: base } = (await (await page.request.get("/api/workspace")).json()) as {
        base_currency: string;
      };
      const numbers = what.cases.map((number) => t("cases.number", { number })).join(", ");

      await page.goto(`/app/clients/${id}`);
      await page
        .locator(".page-header")
        .getByRole("button", { name: t("contacts.deleteCard"), exact: true })
        .click();
      const items = page.getByRole("dialog").locator(".confirm-list li");
      // The cases first, then what they hold, money first.
      const cases = what.cases.length === 1 ? "contacts.deletedWith.case" : "contacts.deletedWith.cases";
      await expect(items.nth(0)).toHaveText(t(cases, { numbers }));
      await expect(items.nth(1)).toHaveText(
        t("cases.deletedWith.payments", {
          n: what.payments,
          total: moneyText(lang, what.payments_total, base),
        }),
      );
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog")).toHaveCount(0);
      expect((await page.request.get(`/api/contacts/${id}`)).status()).toBe(200);
    });
  });
}

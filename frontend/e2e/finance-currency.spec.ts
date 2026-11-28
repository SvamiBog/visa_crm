import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The price of a case in its own currency, and payments as income (pilot fixes 4 and 5,
// 2026-11-19): the specialist picks the price's currency among the ones she accepts, the
// form asks for its rate, and the case shows the price in it; a client's payment is never
// shown with a minus. The test borrows a demo case and puts its price back afterwards.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Finance = { currency: string; rate: string; price: string | null; payments: unknown[] };

async function signIn(page: Page): Promise<void> {
  expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
  const login = await page.request.post("/api/auth/login", {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
}

// The demo's own clients (Telegram demo_<n>_<m>): other specs make cards of their own and
// change them while this one runs, so it borrows only the demo's.
const DEMO = "demo_";
// case-delete.spec.ts opens and deletes a case on the four oldest demo cards meanwhile.
const TAKEN_BY_CASE_DELETE = 4;

/** A demo card with a single case, so its finances are the only ones on the page. */
async function cardWithOneCase(page: Page): Promise<{ contact: string; caseId: string }> {
  const found = await page.request.post("/api/contacts/search", { data: { query: DEMO } });
  expect(found.ok(), await found.text()).toBe(true);
  const items = ((await found.json()).items as { id: string }[])
    .reverse()
    .slice(TAKEN_BY_CASE_DELETE);
  for (const { id } of items) {
    const card = (await (await page.request.get(`/api/contacts/${id}`)).json()) as {
      cases: { id: string }[] | null;
    };
    if (card.cases?.length === 1) return { contact: id, caseId: card.cases[0].id };
  }
  throw new Error("The demo has no card with a single case");
}

// One case for both runs: one after the other.
test.describe.configure({ mode: "serial" });

for (const [lang, width] of [
  ["ru", 390],
  ["en", 1280],
] as const) {
  const t = (key: string, values: Record<string, string> = {}) => {
    if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
    return Object.entries(values).reduce(
      (text, [name, value]) => text.replace(`{{${name}}}`, value),
      LOCALES[lang][key],
    );
  };

  test(`the price in another currency, payments as income (${lang}, ${width}px)`, async ({
    page,
  }) => {
    await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
    await page.setViewportSize({ width, height: 900 });
    await signIn(page);
    const money = (await (await page.request.get("/api/dictionaries/money")).json()) as {
      base_currency: string;
      payment_currencies: string[];
    };
    const base = money.base_currency;
    const other = money.payment_currencies.find((c) => c !== base);
    expect(other, "the demo accepts a currency besides the base one").toBeTruthy();
    const { contact, caseId } = await cardWithOneCase(page);
    const url = `/api/cases/${caseId}/finance`;
    const before = (await (await page.request.get(url)).json()) as Finance;
    const paid = await page.request.post(`/api/cases/${caseId}/payments`, {
      data: { paid_on: "2025-04-07", amount: "150", currency: base, official: true },
    });
    expect(paid.ok(), await paid.text()).toBe(true);
    const payments = ((await paid.json()) as { payments: { id: string }[] }).payments;
    const payment = payments.find(
      (p) => !(before.payments as { id: string }[]).some((q) => q.id === p.id),
    )!;

    try {
      await page.goto(`/app/clients/${contact}?tab=cases`);
      // The card's one case, loaded, before anything is read from it.
      await expect(page.locator("article.case-panel")).toHaveCount(1);
      const section = page.locator("section.finance");
      await expect(section.getByRole("heading", { name: t("finance.heading") })).toBeVisible();

      // A payment is income: no minus in front of it.
      const paymentRows = section.getByRole("row").filter({ hasText: t("finance.official") });
      await expect(paymentRows.first()).toBeVisible();
      for (const cell of await paymentRows.locator("td.money-cell").allTextContents()) {
        expect(cell.trim()).not.toMatch(/^[−-]/);
      }

      await section.getByRole("button", { name: t("reference.edit"), exact: true }).first().click();
      const form = section.locator("form.finance-form");
      await form
        .getByRole("combobox", { name: t("finance.priceCurrency"), exact: true })
        .selectOption(other!);
      // The rate of the price's currency, filled with the site's latest one.
      const rate = form.getByLabel(t("finance.rate", { base }), { exact: true }).first();
      await expect(rate).not.toHaveValue("");
      const price = form.getByLabel(t("finance.priceField", { currency: other! }), { exact: true });
      await price.fill("800");
      await form.getByRole("button", { name: t("common.save"), exact: true }).click();
      await expect(form).toHaveCount(0);

      const priceRow = section.getByRole("row").filter({ hasText: t("finance.price") });
      await expect(priceRow.locator("td.money-cell")).toHaveText(new RegExp(`800\\s${other}$`));
      const saved = (await (await page.request.get(url)).json()) as Finance;
      expect(saved.currency).toBe(other);

      // A payment in the base currency against a price in another one shows its amount,
      // not a rate of 1 (the rate is to the base currency).
      const atOne = t("finance.atRate", { amount: "150", currency: base, rate: "1" });
      await expect(section.getByRole("row").filter({ hasText: `150 ${base}` }).first()).toBeVisible();
      await expect(section.getByRole("row").filter({ hasText: atOne })).toHaveCount(0);
    } finally {
      await page.request.delete(`/api/payments/${payment.id}`);
      await page.request.patch(url, {
        data: {
          currency: before.currency,
          ...(before.currency !== base ? { rate: before.rate } : {}),
          price: before.price,
        },
      });
    }
  });
}

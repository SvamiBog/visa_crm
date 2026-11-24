import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The specialist sets the accounting currency and the tax rate herself (pilot T8,
// 2026-11-20) on «My account»: an empty rate is «not set», 0 is no tax, a new accounting
// currency says what it will recount before she saves it. The change of the currency
// itself is not saved here: it would recount the demo workspace other tests read at the
// same time (backend/tests/test_base_currency.py covers it); its refusal is a stubbed
// answer. A new currency is asked once more before it is saved. A prepayment of the lists
// has its own currency. The case's finance form warns that a new price currency converts
// nothing typed.
// The test puts the tax rate back afterwards.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";
const MONEY = "/api/dictionaries/money";

type Money = { base_currency: string; payment_currencies: string[]; tax_rate: string | null };

async function signIn(page: Page): Promise<void> {
  expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
  const login = await page.request.post("/api/auth/login", {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
}

async function money(page: Page): Promise<Money> {
  return (await (await page.request.get(MONEY)).json()) as Money;
}

/** A demo card with a single case, so its finances are the first on the page; the first
 * such card (finance-currency.spec.ts borrows the last one). */
async function cardWithOneCase(page: Page): Promise<string> {
  const found = await page.request.post("/api/contacts/search", { data: {} });
  expect(found.ok()).toBe(true);
  for (const { id } of (await found.json()).items as { id: string }[]) {
    const card = (await (await page.request.get(`/api/contacts/${id}`)).json()) as {
      cases: unknown[] | null;
    };
    if (card.cases?.length === 1) return id;
  }
  throw new Error("The demo has no card with a single case");
}

// One workspace's tax rate for both runs: one after the other.
test.describe.configure({ mode: "serial" });

for (const [lang, width] of [
  ["ru", 390],
  ["en", 1280],
] as const) {
  const t = (key: string, values: Record<string, string> = {}) => {
    if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
    return Object.entries(values).reduce(
      (text, [name, value]) => text.split(`{{${name}}}`).join(value),
      LOCALES[lang][key],
    );
  };

  test(`the accounting currency and the tax rate (${lang}, ${width}px)`, async ({ page }) => {
    // Settings, the lists and a case's finances in one run: on a loaded CI runner it went
    // past the default 30 s twice (main after PR #160, PR #163), with nothing failing.
    test.slow();
    await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
    await page.setViewportSize({ width, height: 900 });
    await signIn(page);
    const before = await money(page);
    const other = before.payment_currencies.find((c) => c !== before.base_currency);
    expect(other, "the demo accepts a currency besides the accounting one").toBeTruthy();

    try {
      await page.goto("/settings");
      const card = page.locator("form").filter({
        has: page.getByRole("heading", { name: t("settings.money"), exact: true }),
      });
      await expect(card).toBeVisible();
      const base = card.getByLabel(t("cabinet.baseCurrency"), { exact: true });
      const tax = card.getByLabel(t("settings.taxRate"), { exact: true });
      const save = card.getByRole("button", { name: t("common.save"), exact: true });
      await expect(base).toHaveValue(before.base_currency);
      const fits = await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      );
      expect(fits, "the settings page does not scroll sideways").toBe(true);

      // A rate typed with a comma; empty is «not set»; 0 is no tax.
      for (const [typed, stored] of [
        ["12,5", "12.50"],
        ["", null],
        ["0", "0.00"],
      ] as const) {
        await tax.fill(typed);
        const saved = page.waitForResponse(
          (r) => r.url().endsWith(MONEY) && r.request().method() === "PATCH",
        );
        await save.click();
        expect((await saved).ok()).toBe(true);
        await expect(card.getByRole("status")).toHaveText(t("dict.saved"));
        expect((await money(page)).tax_rate, `the rate typed as «${typed}»`).toBe(stored);
      }

      // A rate over 100: the message stands under the field.
      await tax.fill("150");
      await save.click();
      await expect(tax).toHaveAttribute("aria-invalid", "true");
      await expect(card.getByText(t("errors.tax_rate_invalid"), { exact: true })).toBeVisible();
      await tax.fill("0");

      // Another accounting currency: the card says what will be recounted, and the button
      // says it recounts.
      await base.selectOption(other!);
      const warning = t("settings.rebaseWarning", { from: before.base_currency, to: other! });
      await expect(card.getByRole("status")).toHaveText(warning);
      const rebase = card.getByRole("button", {
        name: t("settings.rebase", { currency: other! }),
        exact: true,
      });
      // Asked once more before every amount is counted again: «Cancel» sends nothing. Then,
      // confirmed, the site has no bank rate for one of the amounts: the message stands
      // under the currency, and nothing changes.
      let sent: Record<string, unknown> | null = null;
      await page.route(`**${MONEY}`, async (route) => {
        if (route.request().method() !== "PATCH") return route.continue();
        sent = route.request().postDataJSON() as Record<string, unknown>;
        await route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({ detail: "base_rate_unavailable" }),
        });
      });
      await rebase.click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toContainText(warning);
      await dialog.getByRole("button", { name: t("common.cancel"), exact: true }).click();
      await expect(dialog).toHaveCount(0);
      expect(sent, "nothing is sent on «Cancel»").toBeNull();
      await expect(base, "the new currency stays chosen").toHaveValue(other!);
      await rebase.click();
      await dialog
        .getByRole("button", { name: t("settings.rebase", { currency: other! }), exact: true })
        .click();
      await expect(base).toHaveAttribute("aria-invalid", "true");
      await expect(
        card.getByText(t("errors.base_rate_unavailable"), { exact: true }),
      ).toBeVisible();
      expect(sent, "the new currency is sent").toMatchObject({ base_currency: other });
      await page.unroute(`**${MONEY}`);
      expect((await money(page)).base_currency).toBe(before.base_currency);

      // Back to the current one: no warning, a plain save.
      await base.selectOption(before.base_currency);
      await expect(card.getByText(warning, { exact: true })).toHaveCount(0);
      await expect(save).toBeVisible();

      // A prepayment of the lists is named in its own currency, the base one to start with.
      // Not saved: the lists stay as they are.
      await page.goto("/app/dictionaries");
      await page.getByRole("tab", { name: t("dict.tab.prepayments"), exact: true }).click();
      await page.getByRole("button", { name: t("dict.addPrepayment"), exact: true }).click();
      await expect(page.getByText(t("dict.prepaymentsHint"), { exact: true })).toBeVisible();
      await expect(
        page.getByRole("combobox", { name: t("dict.currency"), exact: true }).last(),
      ).toHaveValue(before.base_currency);

      // A case's price in another currency: what was typed is not converted, the form says so.
      await page.goto(`/app/clients/${await cardWithOneCase(page)}`);
      const section = page.locator("section.finance").first();
      await section.getByRole("button", { name: t("reference.edit"), exact: true }).first().click();
      const form = section.locator("form.finance-form");
      const priceCurrency = form.getByRole("combobox", {
        name: t("finance.priceCurrency"),
        exact: true,
      });
      const current = await priceCurrency.inputValue();
      const next = before.payment_currencies.find((c) => c !== current)!;
      await priceCurrency.selectOption(next);
      const keeps = t("finance.currencyKeepsNumbers", { currency: next });
      const prepayment = form.getByLabel(t("finance.prepaymentField", { currency: next }), {
        exact: true,
      });
      await prepayment.fill("");
      const credit = form.getByLabel(t("finance.consultationField", { currency: next }), {
        exact: true,
      });
      await credit.fill("");
      await expect(form.getByText(keeps, { exact: true })).toHaveCount(0);
      await prepayment.fill("100");
      await expect(form.getByText(keeps, { exact: true })).toBeVisible();
      // Not saved: the case stays as it was.
      await form.getByRole("button", { name: t("common.cancel"), exact: true }).click();
      await expect(form).toHaveCount(0);
    } finally {
      await page.unrouteAll({ behavior: "ignoreErrors" });
      const restored = await page.request.patch(MONEY, { data: { tax_rate: before.tax_rate } });
      expect(restored.ok(), await restored.text()).toBe(true);
    }
  });
}

import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The CRM subscription (roadmap 3.8): the admin adds a tariff and marks a payment of the
// demo specialist, the specialist sees their subscription in «Настройки», and an overdue
// payment shows a notice in the cabinet. Both languages.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo accounts (python -m app.cli seed-demo): visa1 has a paid subscription.
const ADMIN = "admin@demo.visacrm.test";
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

async function logIn(page: Page, email: string, lang: Lang): Promise<void> {
  expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
  await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
  const login = await page.request.post("/api/auth/login", {
    data: { email, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`subscriptions in ${lang}`, () => {
    const t = (key: string, values: Record<string, string | number> = {}) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return Object.entries(values).reduce(
        (text, [name, value]) => text.replace(`{{${name}}}`, String(value)),
        LOCALES[lang][key],
      );
    };

    test("the admin adds a tariff, marks a payment and deletes it", async ({ page }) => {
      await logIn(page, ADMIN, lang);
      const specialists = (await (await page.request.get("/api/admin/specialists")).json()) as {
        tenant_id: string;
        name: string;
        email: string;
      }[];
      const specialist = specialists.find((s) => s.email === EMAIL);
      expect(specialist, `the demo has ${EMAIL}`).toBeTruthy();

      await page.goto(`/admin/subscriptions?specialist=${specialist!.tenant_id}`);
      await page.getByRole("heading", { name: t("subscription.pageTitle"), level: 1 }).waitFor();

      // A tariff: the names are unique, so each run and language adds its own.
      const tariffs = page.locator("section.card").filter({
        has: page.getByRole("heading", { name: t("subscription.tariffs"), exact: true }),
      });
      const name = `E2E ${lang} ${Date.now()}`;
      await tariffs.getByRole("button", { name: t("subscription.addTariff") }).click();
      await tariffs.getByLabel(t("subscription.tariffName")).fill(name);
      await tariffs.getByLabel(t("subscription.price")).fill("25");
      await tariffs.getByRole("button", { name: t("common.save") }).click();
      await expect(tariffs.getByRole("cell", { name, exact: true })).toBeVisible();

      // A payment by the tariff the specialist is on, marked and then deleted again, so
      // the demo subscription stays as it was for the other tests.
      const panel = page.getByRole("region", {
        name: t("subscription.of", { name: specialist!.name }),
      });
      const note = `e2e ${lang} ${Date.now()}`;
      await panel.getByLabel(t("subscription.note")).fill(note);
      await panel.getByRole("button", { name: t("subscription.markPayment"), exact: true }).click();
      const row = panel.getByRole("row").filter({ hasText: note });
      await expect(row, "the payment is in the history").toBeVisible();
      await expect(row.getByRole("cell", { name: t("subscription.monthsShort", { count: 1 }) }))
        .toBeVisible();

      page.once("dialog", (dialog) => void dialog.accept());
      await row.getByRole("button", { name: t("subscription.deletePayment") }).click();
      await expect(row, "a deleted payment leaves the history").toHaveCount(0);
    });

    test("the specialist sees their subscription in the settings", async ({ page }) => {
      await logIn(page, EMAIL, lang);
      await page.goto("/settings");
      const own = page.locator("section.card").filter({
        has: page.getByRole("heading", { name: t("subscription.own"), exact: true }),
      });
      await expect(own.getByText(t("subscription.status.paid"), { exact: true })).toBeVisible();
      await expect(own.getByRole("heading", { name: t("subscription.payments") })).toBeVisible();
      await expect(own.getByRole("button"), "the specialist cannot change it").toHaveCount(0);
    });

    test("an overdue payment shows a notice that leads to the settings", async ({ page }) => {
      await logIn(page, EMAIL, lang);
      // The demo specialist has paid; the notice is checked on an overdue state.
      await page.route("**/api/workspace", async (route) => {
        const response = await route.fetch();
        const json = await response.json();
        json.subscription = { status: "overdue", next_due_on: "2026-10-01", overdue_days: 5 };
        await route.fulfill({ response, json });
      });
      await page.goto("/app");
      const notice = page.getByRole("status").filter({
        hasText: t("subscription.overdueBanner", { days: 5 }),
      });
      await expect(notice).toBeVisible();
      await notice.getByRole("link", { name: t("subscription.overdueLink") }).click();
      await expect(page).toHaveURL(/\/settings$/);
    });
  });
}

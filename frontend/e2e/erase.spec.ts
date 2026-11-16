import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// Deleting all the workspace's data (Settings) is offered only with two-factor sign-in on:
// without it the section says so and has no delete button. Nothing is deleted here — the
// erasure itself is covered by backend/tests/test_workspace_erase.py. Both languages, on a
// computer and on a phone.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo), without two-factor sign-in.
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

const WIDTHS = [1280, 390];

async function logIn(page: Page, lang: Lang): Promise<void> {
  expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
  await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
  const login = await page.request.post("/api/auth/login", {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
  const me = (await login.json()) as { mfa_required?: boolean };
  test.skip(Boolean(me.mfa_required), "the demo accounts have two-factor sign-in on");
}

for (const lang of ["ru", "en"] as const) {
  for (const width of WIDTHS) {
    test(`deleting all data needs two-factor sign-in, ${lang}, ${width}px`, async ({ page }) => {
      const t = (key: string) => {
        if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
        return LOCALES[lang][key];
      };
      await logIn(page, lang);
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/settings");

      const section = page
        .locator("section")
        .filter({ has: page.getByRole("heading", { name: t("settings.erase"), level: 2 }) });
      await expect(section).toBeVisible();
      await expect(section.getByText(t("settings.eraseNeedsTwoFactor"))).toBeVisible();
      await expect(section.getByRole("button", { name: t("settings.eraseStart") })).toHaveCount(0);
      const fits = await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      );
      expect(fits, "no sideways scrolling").toBe(true);
    });
  }
}

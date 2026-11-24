import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// «Выгрузка данных» on the import page (backlog 1.21, docs/data-format.md → «Выгрузка»):
// the workspace in one file, confirmed with the account's password, without the vault's
// passwords by default; «С паролями» also asks for the code (with two-factor sign-in on)
// and warns that the file is not for an AI. «Удаление всех данных» in the settings sends the specialist here first.
// Only reads: the export writes nothing but its audit events. A wrong password is left to
// backend/tests/test_data_export.py: it counts with wrong logins of the shared demo account.
// Both languages, on a computer and on a phone, where the page must not scroll sideways.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};
// The names the server gives the file (app/api/imports.py → export_data).
const FILE: Record<Lang, { plain: RegExp; passwords: RegExp }> = {
  ru: {
    plain: /^VisaCRM-выгрузка-\d{4}-\d{2}-\d{2}\.xlsx$/,
    passwords: /^VisaCRM-выгрузка-с-паролями-\d{4}-\d{2}-\d{2}\.xlsx$/,
  },
  en: {
    plain: /^VisaCRM-export-\d{4}-\d{2}-\d{2}\.xlsx$/,
    passwords: /^VisaCRM-export-with-passwords-\d{4}-\d{2}-\d{2}\.xlsx$/,
  },
};
const WIDTHS = [1280, 390];

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

/** Logged in through the API; true when the account has two-factor sign-in on. */
async function logIn(page: Page, lang: Lang): Promise<boolean> {
  expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
  await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
  const login = await page.request.post("/api/auth/login", {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
  const me = (await login.json()) as { mfa_required?: boolean };
  test.skip(Boolean(me.mfa_required), "the demo accounts have two-factor sign-in on");
  const user = (await (await page.request.get("/api/auth/me")).json()) as {
    two_factor_enabled: boolean;
  };
  return user.two_factor_enabled;
}

for (const lang of ["ru", "en"] as const) {
  for (const width of WIDTHS) {
    test(`the export with and without passwords, ${lang}, ${width}px`, async ({ page }) => {
      const t = (key: string) => {
        if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
        return LOCALES[lang][key];
      };
      const twoFactor = await logIn(page, lang);
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/app/import");

      const section = page
        .locator("section")
        .filter({ has: page.getByRole("heading", { name: t("import.export"), level: 2 }) });
      await expect(section).toBeVisible();
      const withPasswords = section.getByRole("checkbox", { name: t("import.exportWithPasswords") });
      const password = section.getByLabel(t("auth.currentPassword"), { exact: true });
      const submit = section.getByRole("button", { name: t("import.exportSubmit"), exact: true });

      // Without the vault's passwords: the account's password only, never the code.
      await expect(withPasswords).not.toBeChecked();
      await expect(section.getByText(t("import.exportPasswordsWarning"))).toHaveCount(0);
      await expect(section.getByLabel(t("import.exportCode"))).toHaveCount(0);
      await password.fill(PASSWORD);
      let downloading = page.waitForEvent("download");
      await submit.click();
      expect((await downloading).suggestedFilename()).toMatch(FILE[lang].plain);
      await expect(section.getByText(t("import.exported"))).toBeVisible();
      await expect(password).toHaveValue("");

      // With passwords: the warning and, with two-factor sign-in, the code too.
      await withPasswords.check();
      await expect(section.getByText(t("import.exportPasswordsWarning"))).toBeVisible();
      await expect(password).toBeVisible();
      await expect(section.getByLabel(t("import.exportCode"))).toHaveCount(twoFactor ? 1 : 0);
      const fits = await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      );
      expect(fits, "no sideways scrolling").toBe(true);
      test.skip(twoFactor, "no code from the app for the demo account here");
      await password.fill(PASSWORD);
      downloading = page.waitForEvent("download");
      await submit.click();
      expect((await downloading).suggestedFilename()).toMatch(FILE[lang].passwords);
      await expect(password).toHaveValue("");
    });
  }

  test(`deleting all data offers the export first, ${lang}`, async ({ page }) => {
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };
    await logIn(page, lang);
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto("/settings");

    const erase = page
      .locator("section")
      .filter({ has: page.getByRole("heading", { name: t("settings.erase"), level: 2 }) });
    await expect(erase.getByText(t("settings.eraseExportFirst"))).toBeVisible();
    await erase.getByRole("link", { name: t("settings.eraseExportLink"), exact: true }).click();

    await expect(page).toHaveURL(/\/app\/import#export$/);
    await expect(page.getByRole("heading", { name: t("import.export"), level: 2 })).toBeInViewport();
  });
}

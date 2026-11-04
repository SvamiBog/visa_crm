import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// Turning on two-factor sign-in starts with the account's password (roadmap 6.2): a
// session left open on someone else's computer must not be enough to connect another
// authenticator app. The QR code shows only after the right password. Both languages, on
// a computer and on a phone.

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
    test(`turning on two-factor sign-in asks for the password first, ${lang}, ${width}px`, async ({
      page,
    }) => {
      const t = (key: string) => {
        if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
        return LOCALES[lang][key];
      };
      await logIn(page, lang);
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/setup-two-factor");
      await page.getByRole("heading", { name: t("twoFactor.setupTitle"), level: 1 }).waitFor();

      const qrCode = page.getByRole("img", { name: t("twoFactor.qrAlt") });
      const password = page.getByLabel(t("auth.currentPassword"));
      const next = page.getByRole("button", { name: t("twoFactor.continue"), exact: true });
      await expect(qrCode).toHaveCount(0);
      await expect(next).toBeDisabled();

      // A wrong try counts towards the lockout; the demo account's logins in the other
      // tests start the count again.
      await password.fill("not-the-password-1");
      await next.click();
      await expect(page.getByRole("alert")).toHaveText(t("errors.invalid_current_password"));
      await expect(password).toHaveValue("");
      await expect(qrCode).toHaveCount(0);

      await password.fill(PASSWORD);
      await next.click();
      await expect(qrCode).toBeVisible();
      await expect(page.getByLabel(t("twoFactor.stepCode"))).toBeVisible();
      const fits = await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      );
      expect(fits, "no sideways scrolling").toBe(true);

      // Left without a code: two-factor sign-in stays off for the other tests.
      await page.getByRole("link", { name: t("twoFactor.notNow"), exact: true }).click();
      await expect(page).not.toHaveURL(/setup-two-factor/);
    });
  }
}

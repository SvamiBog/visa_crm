import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The first visit carries only everyday work (components/LazyPage.tsx): a rare section
// (here «Аналитика») is a file of its own, fetched when first opened, and only the
// language on screen is fetched. After a deploy an open tab asks for a section's old
// file, which is gone: the page reloads once to get the new build, and if the file still
// does not come, the section says there is no connection, inside the menu. Both languages.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

const ANALYTICS_FILE = /\/assets\/AnalyticsPage-[\w-]+\.js$/;
const LANGUAGE_FILE: Record<Lang, RegExp> = {
  ru: /\/assets\/ru-[\w-]+\.js$/,
  en: /\/assets\/en-[\w-]+\.js$/,
};

async function logIn(page: Page): Promise<void> {
  const login = await page.request.post("/api/auth/login", {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
}

/** The scripts the page asks for from now on. */
function scriptsOf(page: Page): string[] {
  const paths: string[] = [];
  page.on("request", (request) => {
    if (request.resourceType() === "script") paths.push(new URL(request.url()).pathname);
  });
  return paths;
}

const fetched = (paths: string[], file: RegExp) => paths.some((path) => file.test(path));

for (const lang of ["ru", "en"] as const) {
  test.describe(`sections on demand in ${lang}`, () => {
    const other: Lang = lang === "ru" ? "en" : "ru";
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };
    const menu = (page: Page) =>
      page.getByRole("navigation", { name: t("nav.label"), exact: true });
    const heading = (page: Page, key: string) =>
      page.getByRole("heading", { level: 1, name: t(key), exact: true });

    test.beforeEach(async ({ page }) => {
      test.skip(!PASSWORD, "DEMO_PASSWORD is not set");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      await logIn(page);
    });

    test("a rare section is fetched only when it opens", async ({ page }) => {
      const scripts = scriptsOf(page);
      await page.goto("/app");
      await expect(heading(page, "cabinet.homeTitle")).toBeVisible();
      expect(fetched(scripts, ANALYTICS_FILE)).toBe(false);
      expect(fetched(scripts, LANGUAGE_FILE[lang])).toBe(true);
      expect(fetched(scripts, LANGUAGE_FILE[other])).toBe(false);

      await menu(page).getByRole("link", { name: t("nav.analytics"), exact: true }).click();
      await expect(heading(page, "analytics.title")).toBeVisible();
      expect(fetched(scripts, ANALYTICS_FILE)).toBe(true);
    });

    test("a section's file gone after a deploy: one reload, then a message", async ({ page }) => {
      await page.route(ANALYTICS_FILE, (route) => route.fulfill({ status: 404, body: "" }));
      let loads = 0;
      page.on("load", () => {
        loads += 1;
      });
      await page.goto("/app");
      await expect(heading(page, "cabinet.homeTitle")).toBeVisible();
      expect(loads).toBe(1);

      await menu(page).getByRole("link", { name: t("nav.analytics"), exact: true }).click();
      // The first failure reloads the page at the section's address...
      await expect.poll(() => loads).toBe(2);
      await expect(page).toHaveURL(/\/app\/analytics$/);
      // ...the second one, right after, does not reload again: the section says so.
      await expect(page.getByRole("alert")).toHaveText(t("errors.network"));
      await expect(menu(page)).toBeVisible();
      expect(loads).toBe(2);

      // The new build is out: reloading opens the section.
      await page.unroute(ANALYTICS_FILE);
      await page.reload();
      await expect(heading(page, "analytics.title")).toBeVisible();
    });
  });
}

test("the other language is fetched when chosen on the login page", async ({ page }) => {
  // A fresh browser: nothing saved, so the page opens in Russian.
  const scripts = scriptsOf(page);
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: LOCALES.ru["auth.loginTitle"] })).toBeVisible();
  expect(fetched(scripts, LANGUAGE_FILE.en)).toBe(false);

  await page.getByRole("button", { name: LOCALES.ru["lang.switch"], exact: true }).click();
  await expect(page.getByRole("heading", { name: LOCALES.en["auth.loginTitle"] })).toBeVisible();
  expect(fetched(scripts, LANGUAGE_FILE.en)).toBe(true);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
});

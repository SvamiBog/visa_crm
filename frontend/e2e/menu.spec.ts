import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The menu: the pages of everyday work first, the documents and the money under their
// headings, and what is set up once under «Настройки» with a menu of its own. On a phone
// the frequent pages sit on a bar at the bottom and the rest opens with «Ещё». Every
// button that opens a part says so (aria-expanded) and names it (aria-controls). Both
// languages.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist and the employee (python -m app.cli seed-demo); the employee has
// only the clients and the cases.
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const EMPLOYEE_EMAIL = process.env.E2E_EMPLOYEE_EMAIL ?? "visa1.staff@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

const COMPUTER = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };

async function logIn(page: Page, email: string): Promise<void> {
  const login = await page.request.post("/api/auth/login", {
    data: { email, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`menu in ${lang}`, () => {
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };
    const mainMenu = (page: Page) =>
      page.getByRole("navigation", { name: t("nav.label"), exact: true });
    const settingsMenu = (page: Page) =>
      page.getByRole("navigation", { name: t("nav.settings"), exact: true });

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
    });

    test("on a computer the pages are grouped and the settings kept apart", async ({ page }) => {
      await logIn(page, EMAIL);
      await page.setViewportSize(COMPUTER);
      await page.goto("/app");
      const menu = mainMenu(page);
      const link = (key: string) => menu.getByRole("link", { name: t(key), exact: true });

      await expect(link("nav.clients")).toBeVisible();
      const documents = menu.getByRole("group", { name: t("nav.documents"), exact: true });
      for (const key of ["nav.checklists", "nav.templates", "nav.stayCalculator"]) {
        await expect(documents.getByRole("link", { name: t(key), exact: true })).toBeVisible();
      }
      const money = menu.getByRole("group", { name: t("nav.money"), exact: true });
      for (const key of ["nav.registrars", "nav.analytics"]) {
        await expect(money.getByRole("link", { name: t(key), exact: true })).toBeVisible();
      }
      await expect(link("nav.dictionaries"), "what is set up once is not in the menu").toHaveCount(
        0,
      );

      await link("nav.settings").click();
      await expect(page, "«Настройки» opens the first of them").toHaveURL(/\/app\/countries$/);
      const settings = settingsMenu(page);
      for (const key of [
        "nav.countries",
        "nav.dictionaries",
        "nav.employees",
        "nav.import",
        "nav.audit",
        "nav.account",
      ]) {
        await expect(settings.getByRole("link", { name: t(key), exact: true })).toBeVisible();
      }

      const dictionaries = settings.getByRole("link", { name: t("nav.dictionaries"), exact: true });
      await dictionaries.click();
      await expect(page).toHaveURL(/\/app\/dictionaries$/);
      await expect(page.getByRole("heading", { level: 1, name: t("dict.title") })).toBeVisible();
      await expect(dictionaries).toHaveAttribute("aria-current", "page");
      await expect(link("nav.settings"), "the menu marks the section").toHaveAttribute(
        "aria-current",
        "true",
      );
    });

    test("a client's card keeps «Клиенты» marked in the menu", async ({ page }) => {
      await logIn(page, EMAIL);
      await page.setViewportSize(COMPUTER);
      const found = await page.request.post("/api/contacts/search", { data: {} });
      expect(found.ok()).toBe(true);
      const [first] = (await found.json()).items as { id: string }[];
      await page.goto(`/app/clients/${first.id}`);
      await expect(
        mainMenu(page).getByRole("link", { name: t("nav.clients"), exact: true }),
      ).toHaveAttribute("aria-current", "page");
    });

    test("the account opens under the email", async ({ page }) => {
      await logIn(page, EMAIL);
      await page.setViewportSize(COMPUTER);
      await page.goto("/app");
      const menu = mainMenu(page);
      const account = menu.getByRole("button", { name: EMAIL, exact: true });
      const own = menu.getByRole("link", { name: t("nav.account"), exact: true });
      const controls = await account.getAttribute("aria-controls");

      await expect(account).toHaveAttribute("aria-expanded", "false");
      await expect(own, "closed at first").toBeHidden();
      await account.click();
      await expect(account).toHaveAttribute("aria-expanded", "true");
      await expect(page.locator(`[id="${controls}"]`), "it names the part it opens").toBeVisible();
      await expect(menu.getByRole("button", { name: t("auth.logout"), exact: true })).toBeVisible();

      await own.click();
      await expect(page).toHaveURL(/\/settings$/);
      await expect(
        page.getByRole("heading", { level: 1, name: t("settings.title"), exact: true }),
      ).toBeVisible();
      await expect(account, "a link closes it").toHaveAttribute("aria-expanded", "false");
    });

    test("an employee sees only the groups and the settings of their own", async ({ page }) => {
      await logIn(page, EMPLOYEE_EMAIL);
      await page.setViewportSize(COMPUTER);
      await page.goto("/app");
      const menu = mainMenu(page);

      await expect(menu.getByRole("link", { name: t("nav.clients"), exact: true })).toBeVisible();
      await expect(
        menu.getByRole("group", { name: t("nav.money"), exact: true }),
        "a group with none of the employee's pages hides",
      ).toHaveCount(0);
      await menu.getByRole("link", { name: t("nav.settings"), exact: true }).click();
      await expect(page).toHaveURL(/\/settings$/);
      await expect(settingsMenu(page), "one page needs no menu of its own").toHaveCount(0);
    });

    test("on a phone the frequent pages are on a bar, the rest behind «Ещё»", async ({
      page,
    }) => {
      await logIn(page, EMAIL);
      await page.setViewportSize(PHONE);
      await page.goto("/app");
      const bar = page.getByRole("navigation", { name: t("nav.tabs"), exact: true });
      const menu = mainMenu(page);
      const more = bar.getByRole("button", { name: t("nav.more"), exact: true });
      const analytics = menu.getByRole("link", { name: t("nav.analytics"), exact: true });

      for (const key of ["nav.home", "nav.clients", "nav.queuesShort", "nav.bookings"]) {
        await expect(bar.getByRole("link", { name: t(key), exact: true })).toBeVisible();
      }
      await expect(
        bar.getByRole("link", { name: t("nav.home"), exact: true }),
        "the bar marks the open page",
      ).toHaveAttribute("aria-current", "page");
      await expect(more).toHaveAttribute("aria-expanded", "false");
      await expect(more).toHaveAttribute("aria-controls", /.+/);
      await expect(analytics, "the rest of the menu is closed on the first screen").toBeHidden();

      await more.click();
      await expect(more).toHaveAttribute("aria-expanded", "true");
      await expect(analytics).toBeVisible();
      await expect(
        menu.getByRole("link", { name: t("nav.vault"), exact: true }),
        "a page past the bar's four is in the sheet",
      ).toBeVisible();
      await expect(
        menu.getByRole("link", { name: t("nav.clients"), exact: true }),
        "a page of the bar is not repeated in the sheet",
      ).toBeHidden();
      await page.keyboard.press("Escape");
      await expect(more).toHaveAttribute("aria-expanded", "false");
      await expect(more, "Escape gives the focus back to «Ещё»").toBeFocused();

      await more.click();
      await analytics.click();
      await expect(page).toHaveURL(/\/app\/analytics$/);
      await expect(more, "a link closes the sheet").toHaveAttribute("aria-expanded", "false");
      await expect(analytics).toBeHidden();

      await page.setViewportSize(COMPUTER);
      await expect(bar, "a computer has no bottom bar").toBeHidden();
      await expect(analytics, "on a computer the menu is always open").toBeVisible();
    });

    test("on a phone the settings are one row above the page", async ({ page }) => {
      await logIn(page, EMAIL);
      await page.setViewportSize(PHONE);
      await page.goto("/app/audit");
      const settings = settingsMenu(page);
      const audit = settings.getByRole("link", { name: t("nav.audit"), exact: true });
      const links = settings.getByRole("link");

      await expect(audit).toHaveAttribute("aria-current", "page");
      await expect(audit, "the open one is scrolled into view").toBeInViewport();
      const firstBox = await links.first().boundingBox();
      const lastBox = await links.last().boundingBox();
      expect(Math.round(firstBox!.y), "all in one row").toBe(Math.round(lastBox!.y));
      const width = await page.evaluate(() => document.documentElement.scrollWidth);
      expect(width, "the page itself does not scroll sideways").toBeLessThanOrEqual(PHONE.width);
    });
  });
}

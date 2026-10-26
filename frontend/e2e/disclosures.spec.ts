import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// What hides behind a button opens with it (backlog 1.17): the phone's menu, the empty
// fields of a passport and the employees who see a case. Each button says whether its
// part is open (aria-expanded) and which part it opens (aria-controls). Both languages.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo); the demo has an employee too.
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Card = {
  contact: { full_name_latin: string | null };
  questionnaire: { passport_number: string | null };
  cases: unknown[] | null;
};

/** A client in work with a case and a passport of only the Latin name, from the API. */
async function openClient(page: Page): Promise<void> {
  const found = await page.request.post("/api/contacts/search", { data: { tab: "work" } });
  expect(found.ok()).toBe(true);
  const items = (await found.json()).items as { id: string; full_name_latin: string | null }[];
  for (const item of items.filter((i) => i.full_name_latin)) {
    const card = (await (await page.request.get(`/api/contacts/${item.id}`)).json()) as Card;
    if (!card.questionnaire.passport_number && card.cases?.length) {
      await page.goto(`/app/clients/${item.id}`);
      await page.locator("h1").waitFor();
      return;
    }
  }
  throw new Error("The demo has no client in work with a case and a passport to fill");
}

/** The part a disclosure button opens. */
function panelOf(page: Page, button: Locator): Promise<Locator> {
  return button.getAttribute("aria-controls").then((id) => {
    expect(id, "the button names the part it opens").toBeTruthy();
    return page.locator(`[id="${id}"]`);
  });
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`disclosures in ${lang}`, () => {
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
    });

    test("on a phone the menu opens with «Меню» and closes on a link or Escape", async ({
      page,
    }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto("/app");
      const menu = page.getByRole("navigation", { name: t("nav.label") });
      const toggle = menu.getByRole("button", { name: t("nav.menu"), exact: true });
      const clients = menu.getByRole("link", { name: t("nav.clients"), exact: true });

      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await expect(clients, "the menu is closed on the first screen").toBeHidden();

      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-expanded", "true");
      await expect(clients).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await expect(toggle, "Escape gives the focus back to «Меню»").toBeFocused();

      await toggle.click();
      await clients.click();
      await expect(page).toHaveURL(/\/app\/clients$/);
      await expect(toggle, "a link closes the menu").toHaveAttribute("aria-expanded", "false");
      await expect(clients).toBeHidden();

      await page.setViewportSize({ width: 1440, height: 900 });
      await expect(toggle, "on a computer the menu is always open").toBeHidden();
      await expect(clients).toBeVisible();
    });

    test("a passport shows its filled fields, the empty ones on «Показать все поля»", async ({
      page,
    }) => {
      await openClient(page);
      const passport = page
        .locator("section.card")
        .filter({ has: page.getByRole("heading", { name: t("contacts.passport"), exact: true }) });
      const number = passport.getByText(t("contacts.passportNumber"), { exact: true });
      const showAll = passport.getByRole("button", { name: t("contacts.showAllFields") });

      await expect(showAll).toHaveAttribute("aria-expanded", "false");
      await expect(number, "an empty field is not shown").toBeHidden();
      await showAll.click();
      const hideEmpty = passport.getByRole("button", { name: t("contacts.hideEmptyFields") });
      await expect(hideEmpty).toHaveAttribute("aria-expanded", "true");
      await expect(number).toBeVisible();
      await hideEmpty.click();
      await expect(number).toBeHidden();
    });

    test("who sees a case is one line; the checkboxes open with «Изменить доступ»", async ({
      page,
    }) => {
      await openClient(page);
      const change = page.getByRole("button", { name: t("caseAccess.change"), exact: true }).first();
      const panel = await panelOf(page, change);

      await expect(change).toHaveAttribute("aria-expanded", "false");
      await expect(panel).toBeHidden();
      await change.click();
      await expect(change).toHaveAttribute("aria-expanded", "true");
      await expect(panel).toBeVisible();
      await expect(panel.getByRole("checkbox").first()).toBeVisible();
      await change.click();
      await expect(panel).toBeHidden();
    });
  });
}

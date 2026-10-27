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

type CardCases = { cases: { id: string; number: number; destination_id: string }[] | null };
type PackageItem = { name: string; max_age_days: number | null };

/** A Polish case of a client in work (the demo has a checklist for Poland), and the first
 * document of its checklist with a freshness limit. The tests run in parallel, so each
 * language takes its own case (`which`) not to mark the same document at once. */
async function openPackage(
  page: Page,
  which: number,
): Promise<{ number: number; document: string }> {
  const countries = (await (await page.request.get("/api/reference/countries")).json()) as {
    id: string;
    code: string;
  }[];
  const poland = countries.find((c) => c.code === "PL")?.id;
  const found = await page.request.post("/api/contacts/search", { data: { tab: "work" } });
  expect(found.ok()).toBe(true);
  let seen = 0;
  for (const item of (await found.json()).items as { id: string }[]) {
    const card = (await (await page.request.get(`/api/contacts/${item.id}`)).json()) as CardCases;
    for (const c of (card.cases ?? []).filter((c) => c.destination_id === poland)) {
      const result = await page.request.get(`/api/cases/${c.id}/package`);
      expect(result.ok(), await result.text()).toBe(true);
      const dated = ((await result.json()).items as PackageItem[]).find((i) => i.max_age_days);
      if (dated && seen++ === which) {
        await page.goto(`/app/clients/${item.id}`);
        await page.locator("h1").waitFor();
        return { number: c.number, document: dated.name };
      }
    }
  }
  throw new Error("The demo has too few clients in work with a Polish case");
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

    test("a case's documents are one line; the checklist opens with «Показать документы»", async ({
      page,
    }) => {
      const { number, document } = await openPackage(page, lang === "ru" ? 0 : 1);
      const title = `${t("cases.number").replace("{{number}}", String(number))} ·`;
      const panelOfCase = page
        .locator("article.case-panel")
        .filter({ has: page.getByRole("heading", { name: title }) });
      const block = panelOfCase
        .locator("section")
        .filter({ has: page.getByRole("heading", { name: t("package.title"), exact: true }) });
      const show = block.getByRole("button", { name: t("package.show").split("{{count}}")[0] });
      const list = await panelOf(page, show);

      await expect(show).toHaveAttribute("aria-expanded", "false");
      await expect(list, "the checklist is closed at first").toBeHidden();
      await show.click();
      await expect(list).toBeVisible();
      const hide = block.getByRole("button", { name: t("package.hide"), exact: true });
      await expect(hide).toHaveAttribute("aria-expanded", "true");

      // Marking a document changes its status; a dated document needs its date.
      const item = list.getByRole("listitem").filter({ hasText: document });
      const status = item.locator(".chip");
      // By role: the text of the <label> around a <select> takes in its options too.
      const state = item.getByRole("combobox", { name: t("package.state"), exact: true });
      await state.selectOption("missing");
      await expect(status).toHaveText(t("package.status.missing"));
      await state.selectOption("present");
      await expect(status, "«есть» without a date").toHaveText(t("package.status.no_date"));
      const date = item.getByLabel(t("package.documentDate"), { exact: true });
      // Today in UTC is never later than today in the specialist's time zone.
      await date.fill(new Date().toISOString().slice(0, 10));
      await date.blur();
      await expect(status, "the date is saved when the field is left").toHaveText(
        t("package.status.ok"),
      );

      await hide.click();
      await expect(list).toBeHidden();
    });
  });
}

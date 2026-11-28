import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The client's card by tabs (backlog 1.15.17, the specialist's remark of 28.11.2026: «the
// sections merge»). «Клиент» holds the contact with short rows of the cases, the
// questionnaire, the consultations and the notes; «Кейсы» the case blocks; a tab of a module
// turned off is not there. The tab is in the address: it survives a reload and «back», and
// «← Все клиенты» still brings back the list as it was. Every way to a case opens «Кейсы»
// on that case with the focus on its heading, and what is typed on one tab waits there.
// A phone in Russian and a desktop in English; the test makes its own cards and keeps them.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Country = { id: string; code: string; name_ru: string; name_en: string };
type Case = { id: string; number: number; contact_id: string };

async function countries(page: Page): Promise<Map<string, Country>> {
  const response = await page.request.get("/api/reference/countries");
  expect(response.ok(), await response.text()).toBe(true);
  return new Map(((await response.json()) as Country[]).map((c) => [c.code, c]));
}

/** A card with a phone and a Telegram username like a demo client's, so that the specs
 * which look for such a card among the newest still find one. */
async function newContact(page: Page, lastName: string, firstName: string): Promise<string> {
  const digits = String(Math.floor(Math.random() * 1e7)).padStart(7, "0");
  const response = await page.request.post("/api/contacts", {
    data: {
      last_name: lastName,
      first_name: firstName,
      phone: `+37529${digits}`,
      telegram: `tabs_${digits}`,
    },
  });
  expect(response.ok(), await response.text()).toBe(true);
  return ((await response.json()) as { id: string }).id;
}

async function openCases(
  page: Page,
  contactId: string,
  data: Record<string, unknown>,
): Promise<Case[]> {
  const response = await page.request.post(
    `/api/contacts/${contactId}/cases?allow_case_in_work=true`,
    { data },
  );
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as Case[];
}

/** A client with two cases, as in the pilot: Spain in work filed with his wife, and Poland
 * with a visa (folded on «Кейсы»). */
async function clientWithTwoCases(page: Page, stamp: string) {
  const country = await countries(page);
  const [spain, poland, belarus] = [country.get("ES")!, country.get("PL")!, country.get("BY")!];
  const id = await newContact(page, "Вкладкин", stamp);
  const spainCases = await openCases(page, id, {
    destination_id: spain.id,
    submission_country_id: belarus.id,
    applicants: [{ contact_id: id }, { last_name: "Вкладкина", first_name: stamp, label: "жена" }],
  });
  const inWork = spainCases.find((c) => c.contact_id === id)!;
  const wife = spainCases.find((c) => c.contact_id !== id)!;
  const [done] = await openCases(page, id, {
    destination_id: poland.id,
    submission_country_id: belarus.id,
    applicants: [{ contact_id: id }],
  });
  const result = await page.request.patch(`/api/cases/${done.id}`, {
    data: { outcome: "visa", decided_on: new Date().toISOString().slice(0, 10) },
  });
  expect(result.ok(), await result.text()).toBe(true);
  return { id, name: `Вкладкин ${stamp}`, inWork, wife, done, spain, poland };
}

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

for (const [lang, width] of [
  ["ru", 390],
  ["en", 1280],
] as const) {
  test.describe(`the card by tabs (${lang}, ${width}px)`, () => {
    const t = (key: string, values: Record<string, string | number> = {}) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return Object.entries(values).reduce(
        (text, [name, value]) => text.replace(`{{${name}}}`, String(value)),
        LOCALES[lang][key],
      );
    };
    const countryName = (c: Country) => (lang === "en" ? c.name_en : c.name_ru);
    const caseName = (c: Case, country: Country) =>
      `${t("cases.number", { number: c.number })} · ${countryName(country)}`;
    const tab = (page: Page, key: string) =>
      page
        .getByRole("tablist", { name: t("cardTabs.label") })
        .getByRole("tab", { name: t(`cardTabs.${key}`) });
    /** The heading of a case's block: «№12 · Испания», maybe with its purpose after it. */
    const headingOf = (page: Page, c: Case, country: Country): Locator =>
      page.getByRole("heading", {
        level: 3,
        name: new RegExp(`^${escape(caseName(c, country))}( ·|$)`),
      });
    const caseHeading = (page: Page, c: Case, country: Country): Locator =>
      page.locator("article.case-panel").locator(headingOf(page, c, country));
    const onCase = async (page: Page, c: Case, country: Country) => {
      await expect(page).toHaveURL(/\?tab=cases$/);
      await expect(tab(page, "cases")).toHaveAttribute("aria-selected", "true");
      const heading = caseHeading(page, c, country);
      await expect(heading).toBeFocused();
      await expect(heading).toBeInViewport();
    };
    const noSideScroll = async (page: Page) =>
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        ),
        "the page is no wider than the screen",
      ).toBeLessThanOrEqual(0);

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      await page.setViewportSize({ width, height: 900 });
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
    });

    // One client for all of it: the specs run in parallel and several of them pick the
    // newest cards of the index, so this one makes as few as it can.
    test("a client's card by tabs, from the list and back", async ({ page }) => {
      test.setTimeout(90_000);
      const stamp = `${lang}${Date.now()}`;
      const client = await clientWithTwoCases(page, stamp);

      // From the list with a filter and a search, as the specialist opens a card.
      await page.goto("/app/clients?tab=work&view=list");
      await page.getByRole("searchbox").fill(stamp);
      await page.getByRole("link", { name: client.name, exact: true }).click();
      await expect(page.locator("h1")).toHaveText(client.name);

      // The first tab: contact, questionnaire, consultations and notes, nothing of a case.
      await expect(tab(page, "client")).toHaveAttribute("aria-selected", "true");
      await expect(tab(page, "cases")).toContainText("2");
      for (const key of [
        "contacts.details",
        "contacts.questionnaire",
        "contacts.consultations",
        "contacts.timeline",
      ]) {
        await expect(page.getByRole("heading", { level: 2, name: t(key), exact: true })).toBeVisible();
      }
      await expect(page.locator("article.case-panel").first()).toBeHidden();
      await expect(page.getByRole("heading", { name: t("contacts.passport"), exact: true })).toBeHidden();

      // Under the contact, a short row for each case with its stage or result.
      const rows = page.getByRole("navigation", { name: t("cardTabs.casesList") });
      await expect(rows.getByRole("listitem")).toHaveCount(2);
      await expect(
        rows.getByRole("listitem").filter({ hasText: caseName(client.done, client.poland) }),
      ).toContainText(t("cases.outcome.visa"));
      await noSideScroll(page);

      // The folded case with the visa: its row opens «Кейсы», unfolds it and puts the focus on it.
      await rows.getByRole("link", { name: caseName(client.done, client.poland), exact: true }).click();
      await onCase(page, client.done, client.poland);
      await expect(page.getByRole("heading", { level: 2, name: t("contacts.timeline") })).toBeHidden();
      await noSideScroll(page);

      // An amount typed in the case in work waits there while other tabs are looked at.
      const block = page
        .locator("article.case-panel")
        .filter({ has: headingOf(page, client.inWork, client.spain) });
      await block.getByRole("button", { name: t("finance.addPayment"), exact: true }).click();
      const amount = block.locator("form.finance-form").getByLabel(t("finance.amount"), {
        exact: true,
      });
      await amount.fill("123");
      await tab(page, "client").click();
      await expect(page).not.toHaveURL(/tab=/);
      await expect(amount).toBeHidden();
      await tab(page, "documents").click();
      await expect(page).toHaveURL(/\?tab=documents$/);
      await expect(page.getByRole("heading", { name: t("contacts.passport"), exact: true })).toBeVisible();
      await tab(page, "cases").click();
      await expect(amount).toHaveValue("123");

      // After all the switches «← Все клиенты» brings back the list with its filter and search.
      await page.getByRole("link", { name: `← ${t("contacts.backToList")}`, exact: true }).click();
      await expect(page).toHaveURL(/\/app\/clients\?.*tab=work/);
      await expect(page.getByRole("searchbox")).toHaveValue(stamp);
      await expect(page.getByRole("link", { name: client.name, exact: true })).toBeVisible();

      // The tab survives a reload and «back» from another page.
      await page.goto(`/app/clients/${client.id}?tab=documents`);
      await page.reload();
      await expect(tab(page, "documents")).toHaveAttribute("aria-selected", "true");
      await page.goto("/app");
      await page.goBack();
      await expect(page).toHaveURL(new RegExp(`/app/clients/${client.id}\\?tab=documents$`));
      await expect(tab(page, "documents")).toHaveAttribute("aria-selected", "true");

      // A case card of the board opens «Кейсы» on its case (the one with the visa is under
      // «Результат»).
      await page.goto("/app/clients?view=board");
      await page.getByRole("searchbox").fill(stamp);
      const number = t("cases.number", { number: client.inWork.number });
      await page
        .locator("article.board-card")
        .filter({ hasText: new RegExp(`${escape(number)}(?!\\d)`) })
        .getByRole("link", { name: client.name, exact: true })
        .click();
      await onCase(page, client.inWork, client.spain);

      // In the wife's case, her husband filed with her: his card on «Кейсы», on that case.
      await page.goto(`/app/clients/${client.wife.contact_id}?tab=cases`);
      await page.locator(".case-group").getByRole("link", { name: client.name, exact: true }).click();
      await expect(page.locator("h1")).toHaveText(client.name);
      await onCase(page, client.inWork, client.spain);

      // With the vault and the trips turned off their tabs are not there, and the address of
      // one opens «Клиент»; nothing is changed for the other tests running meanwhile.
      await page.route("**/api/workspace", async (route) => {
        const response = await route.fetch();
        const body = (await response.json()) as { modules: string[] };
        const modules = body.modules.filter((m) => m !== "vault" && m !== "stay_calculator");
        await route.fulfill({ response, json: { ...body, modules } });
      });
      await page.goto(`/app/clients/${client.id}?tab=vault`);
      await expect(tab(page, "client")).toHaveAttribute("aria-selected", "true");
      await expect(page.getByRole("heading", { name: t("contacts.timeline") })).toBeVisible();
      await expect(tab(page, "vault")).toHaveCount(0);
      await expect(tab(page, "trips")).toHaveCount(0);
      await expect(tab(page, "cases")).toBeVisible();
      await page.goto(`/app/clients/${client.id}?tab=nonsense`);
      await expect(tab(page, "client")).toHaveAttribute("aria-selected", "true");
      await page.unrouteAll({ behavior: "ignoreErrors" });
    });

    test("«кейс открыт на …» in the index opens that case on «Кейсы»", async ({ page }) => {
      const stamp = `${lang}${Date.now()}`;
      const country = await countries(page);
      const [poland, belarus] = [country.get("PL")!, country.get("BY")!];
      // The daughter came to the consultation; the case is her father's.
      const daughter = await newContact(page, "Вкладкина", `Дочь${stamp}`);
      const consulted = await page.request.post(`/api/contacts/${daughter}/consultations`, {
        data: {
          held_on: new Date().toISOString().slice(0, 10),
          decision: "yes",
          destination_ids: [poland.id],
        },
      });
      expect(consulted.ok(), await consulted.text()).toBe(true);
      const [fatherCase] = await openCases(page, daughter, {
        consultation_id: ((await consulted.json()) as { id: string }).id,
        destination_id: poland.id,
        submission_country_id: belarus.id,
        applicants: [{ last_name: "VKLADKIN", first_name: `OTEC${stamp}` }],
      });

      await page.goto("/app/clients?view=list");
      await page.getByRole("searchbox").fill(`Дочь${stamp}`);
      await page
        .locator(".stage-cell")
        .getByRole("link", { name: `VKLADKIN OTEC${stamp}`, exact: true })
        .click();
      await onCase(page, fatherCase, poland);
    });
  });
}

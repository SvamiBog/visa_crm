import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// A case opened from someone else's consultation (pilot fixes 2 and 8, docs/decisions.md
// → «Пилот: правки визовика (2026-11-19)», T2): the daughter came, the case is her
// father's. Both links are shown, the daughter is «В работе» with «Кейс открыт: …», the
// board's card says «через …», a covered consultation offers no «Создать кейс», a second
// case for someone with a case in work is asked about, and the create form takes the
// country from the consultation (empty without one). Both languages.
//
// The spec makes cards of its own with a unique name and keeps them: other specs borrow
// the demo's cards (search «demo_»), never these.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Case = { id: string; number: number; contact_id: string };

async function countryId(page: Page, code: string): Promise<string> {
  const response = await page.request.get("/api/reference/countries");
  expect(response.ok(), await response.text()).toBe(true);
  const country = ((await response.json()) as { id: string; code: string }[]).find(
    (c) => c.code === code,
  );
  if (!country) throw new Error(`No country ${code}`);
  return country.id;
}

async function newContact(page: Page, lastName: string, firstName: string): Promise<string> {
  const response = await page.request.post("/api/contacts", {
    data: { last_name: lastName, first_name: firstName },
  });
  expect(response.ok(), await response.text()).toBe(true);
  return ((await response.json()) as { id: string }).id;
}

async function consult(page: Page, contactId: string, destinations: string[] = []) {
  const response = await page.request.post(`/api/contacts/${contactId}/consultations`, {
    data: {
      held_on: new Date().toISOString().slice(0, 10),
      decision: "yes",
      destination_ids: destinations,
    },
  });
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as { id: string };
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`consultation and case in ${lang}`, () => {
    const t = (key: string, values: Record<string, string | number> = {}) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return Object.entries(values).reduce(
        (text, [name, value]) => text.replace(`{{${name}}}`, String(value)),
        LOCALES[lang][key],
      );
    };

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
    });

    test("the daughter's consultation and the father's case link each other", async ({
      page,
    }) => {
      const tag = `${lang}${Date.now()}`;
      const daughterName = `Наумик Наталья${tag}`;
      const fatherName = `NAUMIK MIKALAI${tag}`;
      const poland = await countryId(page, "PL");
      const belarus = await countryId(page, "BY");
      const daughter = await newContact(page, "Наумик", `Наталья${tag}`);
      const consultation = await consult(page, daughter, [poland]);
      const opened = await page.request.post(`/api/contacts/${daughter}/cases`, {
        data: {
          consultation_id: consultation.id,
          destination_id: poland,
          submission_country_id: belarus,
          applicants: [{ last_name: "NAUMIK", first_name: `MIKALAI${tag}` }],
        },
      });
      expect(opened.ok(), await opened.text()).toBe(true);
      const [fatherCase] = (await opened.json()) as Case[];

      // Her card: the case opened from her consultation, and no «Создать кейс» there.
      await page.goto(`/app/clients/${daughter}`);
      const consultations = page.locator("ul.consultations");
      await expect(consultations).toContainText(t("cases.openedCase", { number: fatherCase.number }));
      await expect(
        consultations.getByRole("button", { name: t("cases.createFromHere"), exact: true }),
      ).toHaveCount(0);
      await consultations.getByRole("link", { name: fatherName, exact: true }).click();
      await page.getByRole("tab", { name: t("cardTabs.cases") }).click();

      // His case: where it came from, with a link back to her card.
      const panel = page.locator("article.case-panel").first();
      await expect(panel).toContainText(t("cases.source"));
      const back = panel.getByRole("link", { name: daughterName, exact: true });
      await expect(back).toBeVisible();

      // A second case for him is asked about first; «Отмена» creates nothing.
      await page.getByRole("button", { name: t("cases.add"), exact: true }).click();
      const form = page.locator("form").filter({
        has: page.getByRole("heading", { name: t("cases.newTitle"), exact: true }),
      });
      // Without a consultation the visa country is empty, never a default one.
      const country = form.getByLabel(t("cases.country"), { exact: true });
      await expect(country).toHaveValue("");
      await country.selectOption(await countryId(page, "ES"));
      await form.getByRole("button", { name: t("cases.create", { count: 1 }) }).click();
      const ask = page.getByRole("dialog");
      await expect(ask).toContainText(t("cases.createAnother"));
      await expect(ask).toContainText(fatherName);
      await ask.getByRole("button", { name: t("common.cancel"), exact: true }).click();
      await expect(ask).toHaveCount(0);
      await expect(form).toBeVisible();
      await form.getByRole("button", { name: t("common.cancel"), exact: true }).click();
      await expect(page.locator("article.case-panel")).toHaveCount(1);

      await back.click();
      await expect(page.locator("h1")).toContainText(daughterName);

      // The index: she is «В работе» with the father's case.
      await page.goto("/app/clients?tab=work");
      await page.locator('input[type="search"]').fill(tag);
      const row = page.locator("tr").filter({
        has: page.getByRole("link", { name: daughterName, exact: true }),
      });
      await expect(row).toContainText(t("index.caseOpenedFor"));
      await expect(row.getByRole("link", { name: fatherName, exact: true })).toBeVisible();
      await expect(row).not.toContainText(t("index.decidedYes"));

      // The board: one card, the father's, «через» her.
      await page.goto("/app/clients?view=board");
      const card = page
        .locator("article.board-card")
        .filter({ has: page.getByRole("link", { name: fatherName, exact: true }) });
      await expect(card).toContainText(t("index.through"));
      await expect(card.getByRole("link", { name: daughterName, exact: true })).toBeVisible();
      await expect(
        page.locator("article.board-card").filter({ hasText: daughterName }),
      ).toHaveCount(1);
    });

    test("the form from a consultation takes its country and says who applies", async ({
      page,
    }) => {
      const tag = `${lang}${Date.now()}`;
      const france = await countryId(page, "FR");
      const visitor = await newContact(page, "Визит", `Тест${tag}`);
      await consult(page, visitor, [france]);
      await page.goto(`/app/clients/${visitor}`);
      await page
        .locator("ul.consultations")
        .getByRole("button", { name: t("cases.createFromHere"), exact: true })
        .click();
      const form = page.locator("form").filter({
        has: page.getByRole("heading", { name: t("cases.createFromConsultation"), exact: true }),
      });
      await expect(form.getByLabel(t("cases.country"), { exact: true })).toHaveValue(france);
      await expect(form).toContainText(t("cases.applicantsNotTheVisitor"));
      await form.getByRole("button", { name: t("common.cancel"), exact: true }).click();
    });
  });
}

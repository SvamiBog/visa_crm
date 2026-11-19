import { expect, test, type APIRequestContext } from "@playwright/test";
import { readFileSync } from "node:fs";

// The daughter came to the consultation, the case is her father's (pilot remark 1.15.1,
// decisions.md → «Кейс-дубль и консультация за родственника»). After «Создать кейс» her
// card shows where the case went, so it is not opened twice; a second case in work to the
// same country is asked about; a duplicate made anyway is deleted from the case's form;
// her row in the index names his case instead of «yes, no case yet». Both languages.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Case = { id: string; number: number };

async function casesOf(request: APIRequestContext, contactId: string): Promise<Case[]> {
  const card = await request.get(`/api/contacts/${contactId}`);
  expect(card.ok(), await card.text()).toBe(true);
  return ((await card.json()) as { cases: Case[] }).cases;
}

async function newContact(request: APIRequestContext, name: string): Promise<string> {
  const created = await request.post("/api/contacts", { data: { first_name: name } });
  expect(created.ok(), await created.text()).toBe(true);
  return ((await created.json()) as { id: string }).id;
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`a consultation for a relative in ${lang}`, () => {
    const t = (key: string, vars: Record<string, string | number> = {}) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return Object.entries(vars).reduce(
        (text, [name, value]) => text.replaceAll(`{{${name}}}`, String(value)),
        LOCALES[lang][key],
      );
    };
    const stamp = () => `${lang} ${Date.now()} ${test.info().parallelIndex}`;
    let daughter = "";
    let father = "";
    let daughterName = "";
    let fatherName = "";

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => {
        localStorage.setItem("visacrm.lang", l);
        localStorage.setItem("visacrm.clientsView", "list");
      }, lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
      daughterName = `E2E Дочь ${stamp()}`;
      fatherName = `E2E Отец ${stamp()}`;
      daughter = await newContact(page.request, daughterName);
      father = await newContact(page.request, fatherName);
      const linked = await page.request.post(`/api/contacts/${daughter}/relations`, {
        data: { related_id: father, label: "папа", reverse_label: "дочь" },
      });
      expect(linked.ok(), await linked.text()).toBe(true);
      const countries = (await (await page.request.get("/api/reference/countries")).json()) as {
        id: string;
        code: string;
      }[];
      const poland = countries.find((c) => c.code === "PL")!.id;
      const held = await page.request.post(`/api/contacts/${daughter}/consultations`, {
        data: { held_on: new Date().toISOString().slice(0, 10), destination_ids: [poland] },
      });
      expect(held.ok(), await held.text()).toBe(true);
    });

    test.afterEach(async ({ page }) => {
      for (const c of await casesOf(page.request, father)) {
        await page.request.delete(`/api/cases/${c.id}`);
      }
      await page.request.delete(`/api/contacts/${father}`);
      await page.request.delete(`/api/contacts/${daughter}`);
    });

    for (const width of [1280, 390]) {
      test(`the father's case is opened once, a duplicate is asked about, ${width}px`, async ({
        page,
      }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(`/app/clients/${daughter}`);
        const consultations = page.locator("section.card").filter({
          has: page.getByRole("heading", { name: t("contacts.consultations"), exact: true }),
        });
        const cases = page.locator("section.card").filter({
          has: page.getByRole("heading", { name: t("cases.title"), exact: true }),
        });
        const openFromConsultation = async () => {
          await consultations
            .getByRole("button", { name: t("cases.createFromHere"), exact: true })
            .click();
          await cases.getByRole("checkbox", { name: daughterName }).uncheck();
          await cases.getByRole("checkbox", { name: fatherName }).check();
          await cases.getByRole("button", { name: t("cases.create", { count: 1 }) }).click();
        };

        // The case is the father's, and the daughter's consultation says so.
        await openFromConsultation();
        await expect(consultations).toContainText(t("contacts.openedCases_one"));
        await expect(consultations.getByRole("link", { name: fatherName })).toBeVisible();
        const [opened] = await casesOf(page.request, father);

        // Opening it again is asked about, with a way to the father's card.
        await openFromConsultation();
        const asked = cases.getByRole("alert");
        await expect(asked).toContainText(
          t("cases.caseExists", { name: fatherName, number: opened.number }),
        );
        await expect(asked.getByRole("link", { name: t("cases.openExistingCase") })).toBeVisible();
        expect(await casesOf(page.request, father)).toHaveLength(1);
        await asked.getByRole("button", { name: t("cases.createAnyway"), exact: true }).click();
        await expect(asked).toBeHidden();
        await expect.poll(async () => (await casesOf(page.request, father)).length).toBe(2);
        // Other tests open cases at the same time: the duplicate's number is not the next.
        const extra = (await casesOf(page.request, father)).find((c) => c.id !== opened.id)!;

        // Her row in the index names his case.
        await page.goto("/app/clients");
        await page.getByPlaceholder(t("contacts.searchPlaceholder")).fill(daughterName);
        const row = page.getByRole("row").filter({ hasText: daughterName });
        await expect(row).toContainText(
          t("index.caseOf", { number: opened.number, name: fatherName }),
        );
        await expect(row).not.toContainText(t("index.decidedYes"));

        // The father's case names who came; the duplicate is deleted from its form.
        await page.goto(`/app/clients/${father}`);
        const panels = page.locator("article.case-panel");
        await expect(panels).toHaveCount(2);
        await expect(panels.first()).toContainText(t("cases.consultedBy"));
        await expect(panels.first().getByRole("link", { name: daughterName })).toBeVisible();
        const duplicate = panels.first();
        // The case's own «Изменить», not the one of its finances below.
        await duplicate
          .locator(":scope > .card-head")
          .getByRole("button", { name: t("reference.edit"), exact: true })
          .click();
        await duplicate.getByRole("button", { name: t("cases.delete"), exact: true }).click();
        const ask = page.getByRole("dialog");
        await expect(ask).toContainText(t("cases.confirmDelete", { number: extra.number }));
        await ask.getByRole("button", { name: t("confirm.delete"), exact: true }).click();
        await expect(panels).toHaveCount(1);
        expect((await casesOf(page.request, father)).map((c) => c.id)).toEqual([opened.id]);
      });
    }

    test("a case with a payment says why it stays", async ({ page }) => {
      const codes = (await (await page.request.get("/api/reference/countries")).json()) as {
        id: string;
        code: string;
      }[];
      const id = (code: string) => codes.find((c) => c.code === code)!.id;
      const made = await page.request.post(`/api/contacts/${father}/cases`, {
        data: {
          destination_id: id("PL"),
          submission_country_id: id("BY"),
          applicants: [{ contact_id: father }],
        },
      });
      expect(made.ok(), await made.text()).toBe(true);
      const [paidCase] = (await made.json()) as Case[];
      const paid = await page.request.post(`/api/cases/${paidCase.id}/payments`, {
        data: { paid_on: new Date().toISOString().slice(0, 10), amount: "50", currency: "BYN" },
      });
      expect(paid.ok(), await paid.text()).toBe(true);

      await page.goto(`/app/clients/${father}`);
      const panel = page.locator("article.case-panel");
      await panel
        .locator(":scope > .card-head")
        .getByRole("button", { name: t("reference.edit"), exact: true })
        .click();
      await panel.getByRole("button", { name: t("cases.delete"), exact: true }).click();
      await expect(page.getByRole("dialog"), "no question for a case that stays").toHaveCount(0);
      await expect(panel.getByRole("alert")).toContainText(t("errors.case_has_history"));
      expect(await casesOf(page.request, father)).toHaveLength(1);
      // The payment keeps the case and the card: take them away for the next run.
      const finance = await page.request.get(`/api/cases/${paidCase.id}/finance`);
      const { payments } = (await finance.json()) as { payments: { id: string }[] };
      for (const p of payments) await page.request.delete(`/api/payments/${p.id}`);
    });
  });
}

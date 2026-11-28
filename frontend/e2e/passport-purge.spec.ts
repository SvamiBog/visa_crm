import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The passport's data goes a week after the decision (backlog 2.8): two days before,
// «Требует внимания» in the client's card names the fields that will go, with «Отложить
// на 30 дней» and «Удалить сейчас». Put off: the line goes and the passport stays.
// Remove now: the site's window lists the fields first; Escape keeps them, the red
// button removes them and the line goes. Both languages, on a phone and a computer.
//
// Removed (backlog A11): the card shows the passport empty at once, the edit form opened
// afterwards has no removed values, and a form opened before the removal cannot put them
// back on «Save»: the site says the data was removed, the API keeps it empty and the card
// shows it as it is now.
//
// Each test makes a client of its own with a passport and a decided case and deletes it
// afterwards (as confirm-undo.spec.ts does), so the demo's clients stay as they were.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

const PASSPORT = {
  passport_number: "TE1234567",
  passport_issued_on: "2021-03-04",
  passport_expires_on: "2031-03-03",
  passport_issued_by: "МВД",
  nationality: "BY",
  sex: "F",
  personal_number: "3120585A001PB8",
  birth_place: "г. Минск",
};

function daysAgo(days: number): string {
  const day = new Date(Date.now() - days * 86_400_000);
  return day.toISOString().slice(0, 10);
}

/** A client with a passport whose refusal was decided ten days ago and entered now: the
 * passport goes in two days. */
async function decidedClient(request: APIRequestContext, name: string): Promise<string> {
  const created = await request.post("/api/contacts", {
    data: { first_name: name, passport: PASSPORT },
  });
  expect(created.ok(), await created.text()).toBe(true);
  const contactId = ((await created.json()) as { id: string }).id;
  const countries = (await (await request.get("/api/reference/countries")).json()) as {
    id: string;
    code: string;
  }[];
  const code = (c: string) => countries.find((x) => x.code === c)?.id;
  const opened = await request.post(`/api/contacts/${contactId}/cases`, {
    data: {
      destination_id: code("PL"),
      submission_country_id: code("BY"),
      applicants: [{ contact_id: contactId }],
    },
  });
  expect(opened.ok(), await opened.text()).toBe(true);
  const [caseRow] = (await opened.json()) as { id: string }[];
  const decided = await request.patch(`/api/cases/${caseRow.id}`, {
    data: { stage: "result", outcome: "refusal", decided_on: daysAgo(10) },
  });
  expect(decided.ok(), await decided.text()).toBe(true);
  return contactId;
}

async function passportNumber(page: Page, contactId: string): Promise<string | null> {
  const card = await page.request.get(`/api/contacts/${contactId}`);
  expect(card.ok(), await card.text()).toBe(true);
  return ((await card.json()) as { questionnaire: { passport_number: string | null } })
    .questionnaire.passport_number;
}

/** The removed fields that hold something in the API (none after the removal). */
async function removedLeft(page: Page, contactId: string): Promise<string[]> {
  const card = await page.request.get(`/api/contacts/${contactId}`);
  expect(card.ok(), await card.text()).toBe(true);
  const q = ((await card.json()) as { questionnaire: Record<string, string | null> })
    .questionnaire;
  return [
    "passport_number",
    "personal_number",
    "passport_issued_on",
    "passport_expires_on",
    "passport_issued_by",
    "birth_place",
  ].filter((field) => q[field] !== null);
}

for (const [lang, width] of [
  ["ru", 390],
  ["en", 1280],
] as const) {
  test.describe(`the passport after the decision in ${lang}, ${width}px`, () => {
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };
    let contactId = "";

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
      const name = `E2E passport ${lang} ${Date.now()} ${test.info().parallelIndex}`;
      contactId = await decidedClient(page.request, name);
      await page.setViewportSize({ width, height: 900 });
      // The passport is on «Паспорт и документы» (backlog 1.15.17).
      await page.goto(`/app/clients/${contactId}?tab=documents`);
    });

    test.afterEach(async ({ page }) => {
      if (contactId) await page.request.delete(`/api/contacts/${contactId}`);
    });

    const line = (page: Page) =>
      page.locator(".attention-item").filter({
        has: page.getByRole("button", { name: t("attention.passportPostpone") }),
      });

    test("«Put off for 30 days» keeps the passport and takes the line away", async ({
      page,
    }) => {
      await expect(line(page)).toHaveCount(1);
      await expect(line(page)).toContainText(t("attention.passportField.passport_number"));
      await line(page).getByRole("button", { name: t("attention.passportPostpone") }).click();
      await expect(line(page)).toHaveCount(0);
      expect(await passportNumber(page, contactId)).toBe(PASSPORT.passport_number);
    });

    test("«Remove now» asks in the site's window and then removes the passport", async ({
      page,
    }) => {
      const remove = line(page).getByRole("button", {
        name: t("attention.passportPurgeNow"),
        exact: true,
      });
      const ask = page.getByRole("dialog");

      await remove.click();
      await expect(ask).toBeVisible();
      await expect(ask).toContainText(t("attention.passportField.birth_place"));
      await expect(ask).toContainText(t("attention.passportPurgeNote"));
      await page.keyboard.press("Escape");
      await expect(ask).toBeHidden();
      expect(await passportNumber(page, contactId)).toBe(PASSPORT.passport_number);

      await remove.click();
      await ask.getByRole("button", { name: t("confirm.delete"), exact: true }).click();
      await expect(line(page)).toHaveCount(0);
      expect(await passportNumber(page, contactId)).toBeNull();
    });

    const editButton = (page: Page) =>
      page.locator(".page-header").getByRole("button", { name: t("reference.edit"), exact: true });
    const editForm = (page: Page) =>
      page
        .locator("form")
        .filter({ has: page.getByRole("heading", { name: t("contacts.editTitle") }) });

    test("after «Remove now» the card shows no passport and «Save» does not bring it back", async ({
      page,
    }) => {
      const number = page.getByText(PASSPORT.passport_number, { exact: true });
      await expect(number).toBeVisible();
      await line(page).getByRole("button", { name: t("attention.passportPurgeNow"), exact: true }).click();
      await page
        .getByRole("dialog")
        .getByRole("button", { name: t("confirm.delete"), exact: true })
        .click();
      await expect(line(page)).toHaveCount(0);
      // The card is read again: the removed number is not on the screen.
      await expect(number).toHaveCount(0);
      await expect(page.getByText(PASSPORT.birth_place, { exact: true })).toHaveCount(0);

      await editButton(page).click();
      const form = editForm(page);
      await expect(form.getByLabel(t("contacts.passportNumber"), { exact: true })).toHaveValue("");
      await expect(form.getByLabel(t("contacts.birthPlace"), { exact: true })).toHaveValue("");
      await form.getByRole("button", { name: t("common.save"), exact: true }).click();
      await expect(form).toHaveCount(0);
      expect(await removedLeft(page, contactId)).toEqual([]);
    });

    test("a form opened before the removal says so and does not bring the passport back", async ({
      page,
    }) => {
      await editButton(page).click();
      const form = editForm(page);
      await expect(form.getByLabel(t("contacts.passportNumber"), { exact: true })).toHaveValue(
        PASSPORT.passport_number,
      );
      // Removed meanwhile on another screen (or by the daily run).
      const removed = await page.request.post(`/api/reminders/passport/${contactId}/purge`);
      expect(removed.status(), await removed.text()).toBe(204);

      await form.getByRole("button", { name: t("common.save"), exact: true }).click();
      await expect(form.getByText(t("errors.passport_changed"), { exact: true })).toBeVisible();
      expect(await removedLeft(page, contactId)).toEqual([]);
      // The card behind the form is read again, and the form opened anew is empty and saves.
      await expect(page.getByText(PASSPORT.passport_number, { exact: true })).toHaveCount(0);
      await form.getByRole("button", { name: t("common.cancel"), exact: true }).click();
      await editButton(page).click();
      await expect(form.getByLabel(t("contacts.passportNumber"), { exact: true })).toHaveValue("");
      await form.getByRole("button", { name: t("common.save"), exact: true }).click();
      await expect(form).toHaveCount(0);
      expect(await removedLeft(page, contactId)).toEqual([]);
    });
  });
}

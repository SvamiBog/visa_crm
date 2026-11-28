import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// A new client in one go (backlog 1.15.16): the card, its questionnaire, its consultation
// and a note are typed on one screen and kept by one button. A block left as it is creates
// nothing; a mistake in the folded passport opens it with the focus on the field; «Всё
// равно сохранить» keeps every block. The cards made here carry NEW_CARD in the name and
// are deleted at the end (sorting.spec.ts makes none, it reads only the demo's own).

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";
const NEW_CARD = "E2E New Client";

type Card = {
  questionnaire: {
    visas: { country: string | null }[];
    refusals: { country: string }[];
    passport_number: string | null;
  };
  consultations: { destination_ids: string[]; notes: string | null }[];
  notes: { body: string }[];
};

async function signIn(page: Page): Promise<void> {
  expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
  const login = await page.request.post("/api/auth/login", {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
}

function unique(): string {
  return Math.random().toString(36).slice(2, 8);
}

for (const [lang, width] of [
  ["ru", 390],
  ["en", 1280],
] as const) {
  const t = (key: string) => {
    if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
    return LOCALES[lang][key];
  };

  test.describe(`new client in ${lang}, ${width}px`, () => {
    // The cards this test made, deleted whatever happens.
    let made: string[] = [];

    test.beforeEach(async ({ page }) => {
      made = [];
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      await page.setViewportSize({ width, height: 900 });
      await signIn(page);
    });

    test.afterEach(async ({ page }) => {
      for (const id of made) await page.request.delete(`/api/contacts/${id}`);
    });

    async function openForm(page: Page, name: string): Promise<Locator> {
      await page.goto("/app/clients?new=1");
      const form = page.locator("form").filter({
        has: page.getByRole("heading", { name: t("contacts.newTitle"), exact: true }),
      });
      await form.getByLabel(t("contacts.lastName"), { exact: true }).fill(name);
      return form;
    }

    /** Creates the card, waits for it to open and returns its data from the API. */
    async function create(page: Page, form: Locator): Promise<Card> {
      await form.getByRole("button", { name: t("contacts.create"), exact: true }).click();
      await page.waitForURL(/\/app\/clients\/[0-9a-f-]{36}$/);
      const id = page.url().split("/").pop()!;
      made.push(id);
      const card = await page.request.get(`/api/contacts/${id}`);
      expect(card.ok()).toBe(true);
      return (await card.json()) as Card;
    }

    test("one button keeps the questionnaire, the consultation and the note", async ({
      page,
    }) => {
      const form = await openForm(page, `${NEW_CARD} ${unique()}`);
      const visas = form.getByRole("group", { name: t("contacts.visas"), exact: true });
      await visas.getByRole("button", { name: t("contacts.addVisa"), exact: true }).click();
      await visas
        .getByRole("combobox", { name: t("dict.country"), exact: true })
        .selectOption({ index: 1 });
      const refusals = form.getByRole("group", { name: t("contacts.refusals"), exact: true });
      await refusals.getByRole("button", { name: t("contacts.addRefusal"), exact: true }).click();
      await refusals.getByRole("combobox").selectOption({ index: 2 });
      const destination = form.getByLabel(t("contacts.destinations"), { exact: true });
      await destination.selectOption({ index: 1 });
      const consultationNotes = "Едут вдвоём в мае";
      await form.getByLabel(t("contacts.consultationNotes"), { exact: true }).fill(consultationNotes);
      const note = "Пришла из Telegram, просила список документов";
      await form.getByLabel(t("contacts.newNote"), { exact: true }).fill(note);

      const card = await create(page, form);
      expect(card.questionnaire.visas).toHaveLength(1);
      expect(card.questionnaire.refusals).toHaveLength(1);
      expect(card.consultations).toHaveLength(1);
      expect(card.consultations[0].destination_ids).toHaveLength(1);
      expect(card.consultations[0].notes).toBe(consultationNotes);
      expect(card.notes.map((n) => n.body)).toEqual([note]);
      // The card shows what was typed.
      await expect(page.getByText(consultationNotes, { exact: true })).toBeVisible();
      await expect(page.getByText(note, { exact: true })).toBeVisible();
    });

    test("no consultation and no note when their blocks are left as they are", async ({
      page,
    }) => {
      const form = await openForm(page, `${NEW_CARD} ${unique()}`);
      // A wiped date does not make a consultation, nor does it stop the form.
      await form.getByLabel(t("contacts.heldOn"), { exact: true }).fill("");
      const card = await create(page, form);
      expect(card.consultations).toEqual([]);
      expect(card.notes).toEqual([]);
      expect(card.questionnaire.visas).toEqual([]);
    });

    test("a mistake in the folded passport opens it with the focus on the field", async ({
      page,
    }) => {
      const form = await openForm(page, `${NEW_CARD} ${unique()}`);
      const papers = form.getByRole("button", { name: t("newClient.paperwork"), exact: true });
      await expect(papers).toHaveAttribute("aria-expanded", "false");

      // The API turns down a date nobody signs on.
      await papers.click();
      const issued = form.getByLabel(t("contacts.consentSignedOn"), { exact: true });
      await issued.fill("1800-01-01");
      await papers.click();
      await expect(issued).toBeHidden();
      await form.getByRole("button", { name: t("contacts.create"), exact: true }).click();
      await expect(papers).toHaveAttribute("aria-expanded", "true");
      await expect(issued).toHaveAttribute("aria-invalid", "true");
      await expect(issued).toBeFocused();
      await expect(form.getByText(t("errors.field_invalid"), { exact: true })).toBeVisible();

      // The browser's own check: a number with a dash.
      await issued.fill("");
      const number = form.getByLabel(t("contacts.passportNumber"), { exact: true });
      await number.fill("MP-123");
      await papers.click();
      await expect(number).toBeHidden();
      await form.getByRole("button", { name: t("contacts.create"), exact: true }).click();
      await expect(papers).toHaveAttribute("aria-expanded", "true");
      await expect(number).toBeFocused();
      await expect(page, "nothing was created").not.toHaveURL(/\/app\/clients\/[0-9a-f-]{36}$/);
    });

    test("«save anyway» with a shared nick keeps every block", async ({ page }) => {
      const nick = `e2e_new_${unique()}`;
      const first = await page.request.post("/api/contacts", {
        data: { last_name: `${NEW_CARD} ${unique()}`, telegram: nick },
      });
      expect(first.ok(), await first.text()).toBe(true);
      made.push((await first.json()).id);

      const form = await openForm(page, `${NEW_CARD} ${unique()}`);
      const telegram = form.getByLabel(t("contacts.telegram"), { exact: true });
      await telegram.fill(nick);
      const consultationNotes = "Сестра, едут вместе";
      await form.getByLabel(t("contacts.consultationNotes"), { exact: true }).fill(consultationNotes);
      const note = "Общий Telegram с сестрой";
      await form.getByLabel(t("contacts.newNote"), { exact: true }).fill(note);
      await form.getByRole("button", { name: t("contacts.create"), exact: true }).click();
      await expect(telegram).toHaveAttribute("aria-invalid", "true");

      await form.getByRole("button", { name: t("contacts.saveAnyway"), exact: true }).click();
      await page.waitForURL(/\/app\/clients\/[0-9a-f-]{36}$/);
      const id = page.url().split("/").pop()!;
      made.push(id);
      const card = (await (await page.request.get(`/api/contacts/${id}`)).json()) as Card;
      expect(card.consultations.map((c) => c.notes)).toEqual([consultationNotes]);
      expect(card.notes.map((n) => n.body)).toEqual([note]);
    });
  });
}

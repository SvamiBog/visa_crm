import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The day the client is to bring the documents, «Документы → Собрать до» in the case form
// (the specialist's answer to question 26, 2026-11-24, docs/decisions.md → «Ответы
// визовика (2026-11-24)»). She sets it next to the questionnaire's day; the case shows it,
// the family filed together gets the same day, and an emptied field clears it. Phone and
// desktop.
//
// The spec makes a card of its own with a unique name and keeps it: other specs borrow the
// demo's cards (search «demo_»), never this one. The client files with a family member, so
// neither case is a single case in work that the board's test could pick while this runs.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Country = { id: string; code: string };
type Case = { id: string; contact_id: string; documents_due_on: string | null };

async function caseOf(page: Page, id: string): Promise<Case> {
  const response = await page.request.get(`/api/cases/${id}`);
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as Case;
}

/** A day ten days from now, as the date field takes it. */
function inTenDays(): string {
  const day = new Date();
  day.setDate(day.getDate() + 10);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
}

for (const [lang, width] of [
  ["ru", 390],
  ["en", 1280],
] as const) {
  const t = (key: string) => {
    if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
    return LOCALES[lang][key];
  };

  test(`the case keeps the day for the documents, for the whole family (${lang}, ${width}px)`, async ({
    page,
  }) => {
    expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
    await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
    await page.setViewportSize({ width, height: 900 });
    const login = await page.request.post("/api/auth/login", {
      data: { email: EMAIL, password: PASSWORD },
    });
    expect(login.ok(), await login.text()).toBe(true);

    const stamp = `${lang}${Date.now()}`;
    const made = await page.request.post("/api/contacts", {
      data: { last_name: "Документов", first_name: stamp },
    });
    expect(made.ok(), await made.text()).toBe(true);
    const contactId = ((await made.json()) as { id: string }).id;
    const countries = await page.request.get("/api/reference/countries");
    expect(countries.ok(), await countries.text()).toBe(true);
    const code = new Map(((await countries.json()) as Country[]).map((c) => [c.code, c.id]));
    const opened = await page.request.post(
      `/api/contacts/${contactId}/cases?allow_case_in_work=true`,
      {
        data: {
          destination_id: code.get("ES"),
          submission_country_id: code.get("BY"),
          applicants: [
            { contact_id: contactId },
            { last_name: "Документова", first_name: stamp },
          ],
        },
      },
    );
    expect(opened.ok(), await opened.text()).toBe(true);
    const cases = (await opened.json()) as Case[];
    const own = cases.find((c) => c.contact_id === contactId)!;
    const family = cases.find((c) => c.contact_id !== contactId)!;

    await page.goto(`/app/clients/${contactId}`);
    const panel = page.locator("article.case-panel");
    await expect(panel).toHaveCount(1);
    // «Изменить» of the case itself, in its head (its blocks have their own).
    const editCase = panel
      .locator(".head-actions")
      .getByRole("button", { name: t("reference.edit"), exact: true });
    await editCase.click();

    // The documents' own block, right after the questionnaire's, with the hint.
    const documents = panel.getByRole("group", { name: t("cases.documents"), exact: true });
    await expect(documents).toBeVisible();
    const field = documents.getByLabel(t("cases.documentsDueOn"), { exact: true });
    await expect(field).toHaveValue("");
    await expect(documents.getByText(t("cases.documentsDueHint"), { exact: true })).toBeVisible();
    const due = inTenDays();
    await field.fill(due);
    await panel.getByRole("button", { name: t("common.save"), exact: true }).click();
    await expect(documents).toHaveCount(0);

    // The case shows the day; the family filed together has it too.
    const shown = panel.locator(".kv").filter({ hasText: t("cases.documents") });
    await expect(shown).toHaveCount(1);
    await expect(shown).toContainText(t("cases.documentsDue").replace(" {{date}}", ""));
    expect((await caseOf(page, own.id)).documents_due_on).toBe(due);
    expect((await caseOf(page, family.id)).documents_due_on).toBe(due);

    // Emptied, the field clears the day.
    await editCase.click();
    await expect(field).toHaveValue(due);
    await field.fill("");
    await panel.getByRole("button", { name: t("common.save"), exact: true }).click();
    await expect(documents).toHaveCount(0);
    await expect(shown).toHaveCount(0);
    expect((await caseOf(page, own.id)).documents_due_on).toBeNull();

    // Nothing runs off the side of a phone.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, "no sideways scroll").toBeLessThanOrEqual(0);
  });
}

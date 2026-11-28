import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// Clients are not linked to each other (owner's decision 2026-11-24, backlog 1.15.11): a
// family filed together is a submission group. The new case form asks nobody who they are
// to the client: a family member with a card is found by name and ticked, one without a
// card is entered by name alone. «Добавить в группу» finds the family member by name too
// and offers their cases. Both languages.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

// The field the form had for a new applicant; it must not come back.
const OLD_LABEL: Record<Lang, string> = { ru: "Кто он клиенту", en: "Relation to the client" };

type Case = { id: string; contact_id: string; group_id: string | null };

async function asDemoSpecialist(page: Page, lang: Lang): Promise<(key: string) => string> {
  expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
  await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
  const login = await page.request.post("/api/auth/login", {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
  return (key: string) => {
    if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
    return LOCALES[lang][key];
  };
}

/** A card of its own: nobody else has the name, so the search finds only it. */
async function newContact(page: Page, lastName: string, firstName: string): Promise<string> {
  const created = await page.request.post("/api/contacts", {
    data: { last_name: lastName, first_name: firstName },
  });
  expect(created.ok(), await created.text()).toBe(true);
  return ((await created.json()) as { id: string }).id;
}

/** One case to Poland for the client, through the API. */
async function openCase(page: Page, contactId: string): Promise<Case> {
  const countries = (await (await page.request.get("/api/reference/countries")).json()) as {
    id: string;
    code: string;
  }[];
  const code = (c: string) => countries.find((x) => x.code === c)?.id;
  const response = await page.request.post(`/api/contacts/${contactId}/cases`, {
    data: {
      destination_id: code("PL"),
      submission_country_id: code("BY"),
      applicants: [{ contact_id: contactId }],
    },
  });
  expect(response.ok(), await response.text()).toBe(true);
  const [opened] = (await response.json()) as Case[];
  return opened;
}

for (const lang of ["ru", "en"] as const) {
  test(`a family case is opened without saying who is who (${lang})`, async ({ page }) => {
    const t = await asDemoSpecialist(page, lang);
    const tag = `${lang} ${Date.now()}`;
    const motherId = await newContact(page, "Семейная", `Мама ${tag}`);
    const daughterId = await newContact(page, "Семейная", `Дочь ${tag}`);
    const daughterName = `Семейная Дочь ${tag}`;

    await page.goto(`/app/clients/${motherId}?tab=cases`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: t("cases.add"), exact: true }).click();
    const form = page.locator("form").filter({
      has: page.getByRole("heading", { name: t("cases.newTitle"), exact: true }),
    });

    // Without a card: the name alone.
    await form.getByRole("button", { name: t("cases.addApplicant"), exact: true }).click();
    const row = form.locator(".applicant-row");
    await expect(row.getByRole("textbox")).toHaveCount(3);
    await expect(form).not.toContainText(OLD_LABEL[lang]);
    await row.getByRole("button", { name: t("dict.remove"), exact: true }).click();

    // With a card: found by name and ticked.
    await form.getByRole("button", { name: t("cases.addApplicantWithCard"), exact: true }).click();
    await form.getByLabel(t("contacts.findContact"), { exact: true }).fill(daughterName);
    await form
      .getByRole("listitem")
      .filter({ hasText: daughterName })
      .getByRole("button", { name: t("contacts.pick"), exact: true })
      .click();
    await expect(form.getByRole("checkbox", { name: daughterName, exact: true })).toBeChecked();

    await form.getByLabel(t("cases.country"), { exact: true }).selectOption({ index: 1 });
    const created = page.waitForResponse(
      (r) => r.url().includes(`/api/contacts/${motherId}/cases`) && r.request().method() === "POST",
    );
    await form
      .getByRole("button", { name: t("cases.create").replace("{{count}}", "2"), exact: true })
      .click();
    const response = await created;
    expect(response.ok(), await response.text()).toBe(true);
    const [mother, daughter] = (await response.json()) as Case[];
    expect(daughter.contact_id).toBe(daughterId);
    expect(mother.group_id).not.toBeNull();
    expect(daughter.group_id).toBe(mother.group_id);
    const card = (await (await page.request.get(`/api/contacts/${daughterId}`)).json()) as object;
    expect(card).not.toHaveProperty("relations");
  });
}

for (const lang of ["ru", "en"] as const) {
  test(`a case joins a group through a client found by name (${lang})`, async ({ page }) => {
    const t = await asDemoSpecialist(page, lang);
    const tag = `${lang} ${Date.now()}`;
    const motherId = await newContact(page, "Семейная", `Мама ${tag}`);
    const sonId = await newContact(page, "Семейный", `Сын ${tag}`);
    const sonName = `Семейный Сын ${tag}`;
    const motherCase = await openCase(page, motherId);
    const sonCase = await openCase(page, sonId);

    await page.goto(`/app/clients/${motherId}?tab=cases`, { waitUntil: "networkidle" });
    const panel = page.locator("article.case-panel");
    await panel.getByRole("button", { name: t("cases.addToGroup"), exact: true }).click();
    await panel.getByLabel(t("contacts.findContact"), { exact: true }).fill(sonName);
    await panel
      .getByRole("listitem")
      .filter({ hasText: sonName })
      .getByRole("button", { name: t("contacts.pick"), exact: true })
      .click();
    // His only case is offered at once.
    await expect(panel.getByLabel(t("cases.joinWith"), { exact: true })).toHaveValue(sonCase.id);
    const joined = page.waitForResponse(
      (r) => r.url().endsWith(`/api/cases/${motherCase.id}/group`) && r.request().method() === "POST",
    );
    await panel.getByRole("button", { name: t("cases.join"), exact: true }).click();
    expect((await joined).ok()).toBe(true);
    await expect(panel.locator(".case-group")).toContainText(sonName);
  });
}

import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// A visa granted in the client's case is in the visa history of the questionnaire by
// itself (backlog A3, docs/clients-module.md → «Анкета консультации»): the specialist does
// not ask a returning client about a visa she arranged herself. The line reads like one
// written by hand — country, kind, purpose, term, «действует» — and names its case; the
// name opens that case lower in the card. Changing the result changes the line at once.
// Phone and desktop.
//
// The spec makes cards of its own with a unique name and keeps them: other specs borrow
// the demo's cards (search «demo_»), never these. The client files with a family member,
// so no case here is a single case in work that the board's test could pick meanwhile.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Country = { id: string; code: string; name_ru: string; name_en: string };
type Purpose = { id: string; name: string; kind: string | null };
type Case = { id: string; number: number; contact_id: string };

/** A day counted from today, as the API takes it. The term below is wide enough for the
 * browser's day and the workspace's to differ. */
function day(offset: number): string {
  const date = new Date();
  date.setDate(date.getDate() + offset);
  return date.toISOString().slice(0, 10);
}

async function json<T>(response: Awaited<ReturnType<Page["request"]["get"]>>): Promise<T> {
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as T;
}

/** The case's block on the card, found by the number in its head. */
function block(page: Page, title: string): Locator {
  return page
    .locator("article.case-panel")
    .filter({ has: page.getByRole("heading", { level: 3, name: title }) });
}

for (const [lang, width] of [
  ["ru", 390],
  ["en", 1280],
] as const) {
  const t = (key: string, values: Record<string, string | number> = {}) => {
    if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
    return Object.entries(values).reduce(
      (text, [name, value]) => text.replace(`{{${name}}}`, String(value)),
      LOCALES[lang][key],
    );
  };

  test(`a visa granted in a case is in the questionnaire's visas (${lang}, ${width}px)`, async ({
    page,
  }) => {
    expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
    await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
    await page.setViewportSize({ width, height: 900 });
    const login = await page.request.post("/api/auth/login", {
      data: { email: EMAIL, password: PASSWORD },
    });
    expect(login.ok(), await login.text()).toBe(true);

    // A returning client: Poland for tourism, with a visa; Spain in work now.
    const stamp = `${lang}${Date.now()}`;
    const contact = await json<{ id: string }>(
      await page.request.post("/api/contacts", {
        data: { last_name: "Визов", first_name: stamp },
      }),
    );
    const countries = new Map(
      (await json<Country[]>(await page.request.get("/api/reference/countries"))).map((c) => [
        c.code,
        c,
      ]),
    );
    const [poland, spain, belarus] = ["PL", "ES", "BY"].map((code) => countries.get(code)!);
    const purposes = await json<Purpose[]>(await page.request.get("/api/dictionaries/purposes"));
    const tourism = purposes.find((p) => p.kind === "tourism");
    expect(tourism, "the demo has a purpose for tourism").toBeTruthy();
    const open = async (destination: Country, family: object, purposeId: string | null) =>
      (
        await json<Case[]>(
          await page.request.post(`/api/contacts/${contact.id}/cases?allow_case_in_work=true`, {
            data: {
              destination_id: destination.id,
              submission_country_id: belarus.id,
              purpose_id: purposeId,
              applicants: [{ contact_id: contact.id }, family],
            },
          }),
        )
      ).find((c) => c.contact_id === contact.id)!;
    const visaCase = await open(
      poland,
      { last_name: "Визова", first_name: stamp, label: "жена" },
      tourism!.id,
    );
    await json(
      await page.request.patch(`/api/cases/${visaCase.id}`, {
        data: {
          outcome: "visa",
          visa_entries: "multiple",
          visa_valid_from: day(-30),
          visa_valid_to: day(335),
        },
      }),
    );
    const wife = (
      await json<{ group: { contact_id: string }[] }>(
        await page.request.get(`/api/cases/${visaCase.id}`),
      )
    ).group[0].contact_id;
    await open(spain, { contact_id: wife }, null);

    await page.goto(`/app/clients/${contact.id}`);
    const questionnaire = page
      .locator("section.card")
      .filter({ has: page.getByRole("heading", { level: 2, name: t("contacts.questionnaire") }) });
    const visas = questionnaire
      .locator(".q-section")
      .filter({ has: page.getByRole("heading", { name: t("contacts.visas") }) });

    // One line, like a visa written by hand, and the case it came from.
    const line = visas.getByRole("listitem");
    await expect(line).toHaveCount(1);
    await expect(line.getByText(lang === "en" ? poland.name_en : poland.name_ru)).toBeVisible();
    await expect(line.getByText(t("contacts.visaKind.schengen"), { exact: true })).toBeVisible();
    await expect(line.getByText(t("purposeKind.tourism"), { exact: true })).toBeVisible();
    await expect(line.getByText(t("contacts.visaActive"), { exact: true })).toBeVisible();
    const toCase = line.getByRole("link", {
      name: t("contacts.visaCase", { number: visaCase.number }),
      exact: true,
    });
    await expect(toCase).toBeVisible();

    // The case is folded (it has a result and the client has another one) and on «Кейсы»:
    // the line's link opens that tab, unfolds the case and brings it into view.
    const name = lang === "en" ? poland.name_en : poland.name_ru;
    const caseBlock = block(
      page,
      `${t("cases.number", { number: visaCase.number })} · ${name} · ${tourism!.name}`,
    );
    const tabs = page.getByRole("tablist", { name: t("cardTabs.label") });
    await tabs.getByRole("tab", { name: t("cardTabs.cases") }).click();
    await expect(
      caseBlock.getByRole("button", { name: t("caseBlocks.expand"), exact: true }),
    ).toHaveAttribute("aria-expanded", "false");
    await tabs.getByRole("tab", { name: t("cardTabs.client"), exact: true }).click();
    await expect(toCase).toBeVisible();
    await toCase.click();
    await expect(page).toHaveURL(/\?tab=cases$/);
    await expect(
      caseBlock.getByRole("button", { name: t("caseBlocks.collapse"), exact: true }),
    ).toHaveAttribute("aria-expanded", "true");
    await expect(caseBlock.getByRole("heading", { level: 3 })).toBeInViewport();

    // Nothing runs off the side of a phone.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, "no sideways scroll").toBeLessThanOrEqual(0);

    // The result corrected to a refusal in the case: the visa leaves the history at once.
    await caseBlock
      .locator(".case-head")
      .getByRole("button", { name: t("reference.edit"), exact: true })
      .click();
    await caseBlock
      .getByRole("combobox", { name: t("cases.outcomeLabel"), exact: true })
      .selectOption({ label: t("cases.outcome.refusal") });
    await caseBlock.getByRole("button", { name: t("common.save"), exact: true }).click();
    await expect(caseBlock.getByRole("button", { name: t("common.save"), exact: true })).toHaveCount(0);
    // The questionnaire is on «Клиент» (backlog 1.15.17).
    await page.getByRole("tab", { name: t("cardTabs.client"), exact: true }).click();
    await expect(visas).toHaveCount(0);
    await expect(
      questionnaire.locator(".kv").filter({ hasText: t("contacts.visas") }),
    ).toContainText("—");
  });
}

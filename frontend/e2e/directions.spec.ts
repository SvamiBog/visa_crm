import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The specialist sets up her own directions (backlog 1.20): the form adds one destination
// with its booking and cities, and the new case form then offers the city.
// Both languages; each takes its own destination, as the tests run in parallel.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo); her directions are from Belarus.
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

const SUBMISSION = "RS";
const DESTINATION: Record<Lang, string> = { ru: "JP", en: "AU" };
const CITY: Record<Lang, string> = { ru: "Белград", en: "Novi Sad" };

type Country = { id: string; code: string; name_ru: string; name_en: string };
type Rule = { id: string; destination_id: string; submission_id: string };

async function countries(request: APIRequestContext): Promise<Map<string, Country>> {
  const list = (await (await request.get("/api/reference/countries")).json()) as Country[];
  return new Map(list.map((c) => [c.code, c]));
}

/** Directions left by an earlier run on the same stand go first. */
async function removeDirections(request: APIRequestContext, codes: string[]): Promise<void> {
  const byCode = await countries(request);
  const ids = new Set(codes.map((code) => byCode.get(code)?.id));
  const rules = (await (
    await request.get("/api/reference/rules?include_inactive=true")
  ).json()) as Rule[];
  for (const rule of rules) {
    if (rule.submission_id === byCode.get(SUBMISSION)?.id && ids.has(rule.destination_id)) {
      expect((await request.delete(`/api/reference/rules/${rule.id}`)).ok()).toBe(true);
    }
  }
}

/** A select by its label: a wrapping label's name also carries the chosen option. */
function select(scope: Page | Locator, label: string): Locator {
  return scope.getByRole("combobox", { name: new RegExp(`^${label}`) });
}

async function openCard(page: Page): Promise<void> {
  const found = await page.request.post("/api/contacts/search", { data: {} });
  expect(found.ok()).toBe(true);
  const [first] = (await found.json()).items as { id: string }[];
  await page.goto(`/app/clients/${first.id}`);
  await page.locator("h1").waitFor();
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`directions in ${lang}`, () => {
    const t = (key: string, values: Record<string, string | number> = {}) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return Object.entries(values).reduce(
        (text, [name, value]) => text.replace(`{{${name}}}`, String(value)),
        LOCALES[lang][key],
      );
    };
    const countryName = (c: Country | undefined) => (lang === "en" ? c?.name_en : c?.name_ru) ?? "";

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
      await removeDirections(page.request, [DESTINATION[lang]]);
    });

    test.afterEach(async ({ page }) => {
      await removeDirections(page.request, [DESTINATION[lang]]);
    });

    test("a direction is added for one country, the case form offers its city", async ({ page }) => {
      const byCode = await countries(page.request);
      const country = byCode.get(DESTINATION[lang]);
      const serbia = byCode.get(SUBMISSION);

      await page.goto("/app/countries");
      await page.getByRole("button", { name: t("directions.add"), exact: true }).click();
      const form = page.locator("form.form-card");
      await select(form, t("reference.submissionCountry")).selectOption(serbia!.id);
      await select(form, t("directions.destination")).selectOption(country!.id);
      // The radio's name is the way of booking and its explanation.
      await form.getByRole("radio", { name: new RegExp(`^${t("reference.booking.embassy")}`) }).check();
      await form.getByLabel(t("directions.city"), { exact: true }).fill(CITY[lang]);
      await form.getByLabel(t("directions.city"), { exact: true }).press("Enter");
      await expect(form.locator(".chip", { hasText: CITY[lang] })).toBeVisible();
      await form.getByRole("button", { name: t("directions.create") }).click();

      await expect(form).toBeHidden();
      await page
        .getByRole("tab", { name: new RegExp(t("directions.from", { country: countryName(serbia) })) })
        .click();
      const row = page.getByRole("row").filter({ hasText: countryName(country) });
      await expect(row).toContainText(CITY[lang]);
      await expect(row).toContainText(t("reference.booking.embassy"));

      await openCard(page);
      await page.getByRole("button", { name: t("cases.add"), exact: true }).click();
      await select(page, t("cases.country")).selectOption(country!.id);
      await select(page, t("cases.submissionCountry")).selectOption(serbia!.id);
      await expect(
        select(page, t("cases.city")).getByRole("option", { name: CITY[lang] }),
      ).toHaveCount(1);
    });
  });
}

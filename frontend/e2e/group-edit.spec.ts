import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// A family filed together is edited as one (owner's decision 2026-11-15, docs/decisions.md
// → «Правка группы подачи»): the case form has «То же для всей группы» ticked, and the
// submission date entered in the mother's case goes to her son's case too. Untick it, and
// only her case changes. Both languages.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Case = { id: string; contact_id: string; submission_on: string | null; stage: string };

/** A mother and her son with one case each, in one submission group, made through the API. */
async function family(page: Page, lang: Lang): Promise<{ mother: Case; son: Case }> {
  const countries = (await (await page.request.get("/api/reference/countries")).json()) as {
    id: string;
    code: string;
  }[];
  const code = (c: string) => countries.find((x) => x.code === c)?.id;
  const created = await page.request.post("/api/contacts", {
    data: { last_name: "Семейная", first_name: `Проверка ${lang} ${Date.now()}` },
  });
  expect(created.ok(), await created.text()).toBe(true);
  const motherId = ((await created.json()) as { id: string }).id;
  const cases = await page.request.post(`/api/contacts/${motherId}/cases`, {
    data: {
      destination_id: code("PL"),
      submission_country_id: code("BY"),
      applicants: [{ contact_id: motherId }, { last_name: "Семейный", first_name: "Сын" }],
    },
  });
  expect(cases.ok(), await cases.text()).toBe(true);
  const [mother, son] = (await cases.json()) as Case[];
  return { mother, son };
}

async function caseOf(page: Page, id: string): Promise<Case> {
  return (await (await page.request.get(`/api/cases/${id}`)).json()) as Case;
}

for (const lang of ["ru", "en"] as const) {
  test(`an edit of one case goes to the whole family (${lang})`, async ({ page }) => {
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };
    expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
    await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
    const login = await page.request.post("/api/auth/login", {
      data: { email: EMAIL, password: PASSWORD },
    });
    expect(login.ok(), await login.text()).toBe(true);
    const { mother, son } = await family(page, lang);
    const sonName = "Семейный Сын";

    // The card's blocks load one after another and push the case down: clicks wait until
    // they are all in.
    await page.goto(`/app/clients/${mother.contact_id}`, { waitUntil: "networkidle" });
    const panel = page.locator("article.case-panel");
    const save = async () => {
      const saved = page.waitForResponse(
        (r) => r.url().endsWith(`/api/cases/${mother.id}`) && r.request().method() === "PATCH",
      );
      await panel.getByRole("button", { name: t("common.save"), exact: true }).click();
      expect((await saved).ok()).toBe(true);
    };
    // «Изменить» of the case itself, in its own heading (finances and the rest have theirs).
    const edit = panel.locator(":scope > .card-head").getByRole("button", {
      name: t("reference.edit"),
      exact: true,
    });
    await edit.click();
    const whole = panel.getByRole("checkbox", {
      name: t("cases.applyToGroup").replace("{{names}}", sonName),
      exact: true,
    });
    await expect(whole).toBeChecked();
    await panel.getByLabel(t("cases.submissionOn"), { exact: true }).fill("2026-12-01");
    await save();

    // The son's case took the date and moved on to collecting documents with his mother.
    const sonNow = await caseOf(page, son.id);
    expect([sonNow.submission_on, sonNow.stage]).toEqual(["2026-12-01", "documents"]);

    // Unticked, the edit stays in the mother's case.
    await edit.click();
    await whole.uncheck();
    await panel.getByLabel(t("cases.submissionOn"), { exact: true }).fill("2026-12-05");
    await save();
    expect((await caseOf(page, mother.id)).submission_on).toBe("2026-12-05");
    expect((await caseOf(page, son.id)).submission_on).toBe("2026-12-01");
  });
}

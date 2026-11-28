import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// A family filed together is edited as one (owner's decision 2026-11-15, docs/decisions.md
// → «Правка группы подачи»): the case form has «То же для всей группы» ticked, and the
// submission date entered in the mother's case goes to her son's case too. Untick it, and
// only her case changes. The result has a mark of its own (pilot fix 7): entered in the
// mother's case, it goes into the son's case, which has none yet, and not into the
// daughter's, which has her own. Both languages.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Case = {
  id: string;
  contact_id: string;
  submission_on: string | null;
  stage: string;
  outcome: string | null;
  decided_on: string | null;
};

/** A mother and her son (and a daughter, when asked) with one case each, in one
 * submission group, made through the API. */
async function family(
  page: Page,
  lang: Lang,
  withDaughter = false,
): Promise<{ mother: Case; son: Case; daughter?: Case }> {
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
      applicants: [
        { contact_id: motherId },
        { last_name: "Семейный", first_name: "Сын" },
        ...(withDaughter ? [{ last_name: "Семейная", first_name: "Дочь" }] : []),
      ],
    },
  });
  expect(cases.ok(), await cases.text()).toBe(true);
  const [mother, son, daughter] = (await cases.json()) as Case[];
  return { mother, son, daughter };
}

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

async function caseOf(page: Page, id: string): Promise<Case> {
  return (await (await page.request.get(`/api/cases/${id}`)).json()) as Case;
}

for (const lang of ["ru", "en"] as const) {
  test(`an edit of one case goes to the whole family (${lang})`, async ({ page }) => {
    const t = await asDemoSpecialist(page, lang);
    const { mother, son } = await family(page, lang);
    const sonName = "Семейный Сын";

    // The card's blocks load one after another and push the case down: clicks wait until
    // they are all in.
    await page.goto(`/app/clients/${mother.contact_id}?tab=cases`, { waitUntil: "networkidle" });
    const panel = page.locator("article.case-panel");
    const save = async () => {
      const saved = page.waitForResponse(
        (r) => r.url().endsWith(`/api/cases/${mother.id}`) && r.request().method() === "PATCH",
      );
      await panel.getByRole("button", { name: t("common.save"), exact: true }).click();
      expect((await saved).ok()).toBe(true);
    };
    // «Изменить» of the case itself, in its own heading (finances and the rest have theirs).
    const edit = panel.locator(":scope > .case-head").getByRole("button", {
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

for (const lang of ["ru", "en"] as const) {
  test(`the same result goes to the family without one (${lang})`, async ({ page }) => {
    const t = await asDemoSpecialist(page, lang);
    const { mother, son, daughter } = await family(page, lang, true);
    if (daughter === undefined) throw new Error("no daughter's case");
    const refused = await page.request.patch(`/api/cases/${daughter.id}`, {
      data: { outcome: "refusal", decided_on: "2026-09-01" },
    });
    expect(refused.ok(), await refused.text()).toBe(true);

    await page.goto(`/app/clients/${mother.contact_id}?tab=cases`, { waitUntil: "networkidle" });
    const panel = page.locator("article.case-panel");
    const edit = panel.locator(":scope > .case-head").getByRole("button", {
      name: t("reference.edit"),
      exact: true,
    });
    await edit.click();
    // The mark appears with the result, ticked, and names only those without one.
    const same = panel.getByRole("checkbox", {
      name: t("cases.resultToGroup").replace("{{names}}", "Семейный Сын"),
      exact: true,
    });
    await expect(same).toHaveCount(0);
    await panel
      .getByRole("combobox", { name: t("cases.outcomeLabel"), exact: true })
      .selectOption("visa");
    await expect(same).toBeChecked();
    await panel.getByLabel(t("cases.decidedOn"), { exact: true }).fill("2026-09-10");
    // The card is fetched again after the save. «Изменить» pressed before it comes back
    // must open the saved case, not the one before the save: saving that form again would
    // wipe the result. The fetch is held back here to make that moment certain.
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route(`**/api/contacts/${mother.contact_id}`, async (route) => {
      await held;
      await route.continue();
    });
    const saved = page.waitForResponse(
      (r) => r.url().endsWith(`/api/cases/${mother.id}`) && r.request().method() === "PATCH",
    );
    await panel.getByRole("button", { name: t("common.save"), exact: true }).click();
    expect((await saved).ok()).toBe(true);

    const sonNow = await caseOf(page, son.id);
    expect([sonNow.outcome, sonNow.decided_on, sonNow.stage]).toEqual([
      "visa",
      "2026-09-10",
      "result",
    ]);
    const daughterNow = await caseOf(page, daughter.id);
    expect([daughterNow.outcome, daughterNow.decided_on]).toEqual(["refusal", "2026-09-01"]);

    // Opened again, with every result in, the form offers no copy.
    await edit.click();
    await expect(
      panel.getByRole("combobox", { name: t("cases.outcomeLabel"), exact: true }),
    ).toHaveValue("visa");
    const prefix = t("cases.resultToGroup").split("{{names}}")[0];
    const anySame = new RegExp(`^${prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`);
    await expect(panel.getByRole("checkbox", { name: anySame })).toHaveCount(0);
    await expect(panel.getByLabel(t("cases.decidedOn"), { exact: true })).toHaveValue(
      "2026-09-10",
    );
    release();
    await page.unrouteAll({ behavior: "wait" });
  });
}

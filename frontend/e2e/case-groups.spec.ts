import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// A case given to an employee opens its whole submission group to them, so only the
// specialist puts cases in a group or takes them out (security audit run-1, M1 and L1):
// the employee sees who the case is filed with, but no «Добавить в группу» or «Убрать из
// группы». The specialist has both on the same card. Both languages.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist and the employee (python -m app.cli seed-demo); the employee is
// given every third case in work, families among them.
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const EMPLOYEE_EMAIL = process.env.E2E_EMPLOYEE_EMAIL ?? "visa1.staff@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type BoardCase = { id: string; number: number; contact_id: string; group_id: string | null };

async function logIn(page: Page, email: string): Promise<void> {
  await page.context().clearCookies();
  const login = await page.request.post("/api/auth/login", {
    data: { email, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
}

/** A case given to the employee, one filed with a family when there is one. The board
 * opens no card, so the employee's daily card limit is not spent on the search. */
async function givenCase(page: Page): Promise<BoardCase> {
  const board = await page.request.get("/api/cases/board");
  expect(board.ok(), await board.text()).toBe(true);
  const cases = ((await board.json()) as { cases: BoardCase[] }).cases;
  expect(cases.length, "the demo employee has cases").toBeGreaterThan(0);
  return cases.find((c) => c.group_id !== null) ?? cases[0];
}

for (const lang of ["ru", "en"] as const) {
  test(`only the specialist changes a submission group (${lang})`, async ({ page }) => {
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };
    expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
    await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
    await logIn(page, EMPLOYEE_EMAIL);
    const given = await givenCase(page);
    const title = `${t("cases.number").replace("{{number}}", String(given.number))} ·`;
    const panel = page.locator("article.case-panel").filter({
      has: page.getByRole("heading", { name: title }),
    });
    const addToGroup = panel.getByRole("button", { name: t("cases.addToGroup"), exact: true });
    const leaveGroup = panel.getByRole("button", { name: t("cases.leaveGroup"), exact: true });

    await page.goto(`/app/clients/${given.contact_id}?tab=cases`, { waitUntil: "networkidle" });
    await expect(panel).toBeVisible();
    if (given.group_id !== null) {
      await expect(
        panel.locator(".case-group"),
        "the employee still sees who the case is filed with",
      ).toContainText(t("cases.group"));
    }
    await expect(addToGroup).toHaveCount(0);
    await expect(leaveGroup).toHaveCount(0);

    await logIn(page, EMAIL);
    await page.goto(`/app/clients/${given.contact_id}?tab=cases`, { waitUntil: "networkidle" });
    await expect(addToGroup).toBeVisible();
    await expect(leaveGroup).toHaveCount(given.group_id !== null ? 1 : 0);
  });
}

import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// A case opened by mistake, a duplicate (pilot fix 1, docs/decisions.md → «Пилот: правки
// визовика (2026-11-19)»): «Создать кейсы» sends one request however fast it is pressed
// and the form closes after it; «Удалить кейс» asks in the site's window, naming the
// case, and the case goes only after the red button. Both languages.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";
// The demo's own clients (Telegram demo_<n>_<m>): other specs make and delete cards of
// their own, so this one opens its case on a demo card and deletes only that case.
const DEMO = "demo_";

async function demoCard(page: Page, index: number): Promise<string> {
  const found = await page.request.post("/api/contacts/search", { data: { query: DEMO } });
  expect(found.ok(), await found.text()).toBe(true);
  const items = (await found.json()).items as { id: string }[];
  expect(items.length).toBeGreaterThan(index);
  return items[items.length - 1 - index].id;
}

async function countryId(page: Page, code: string): Promise<string> {
  const response = await page.request.get("/api/reference/countries");
  expect(response.ok(), await response.text()).toBe(true);
  const country = ((await response.json()) as { id: string; code: string }[]).find(
    (c) => c.code === code,
  );
  if (!country) throw new Error(`No country ${code}`);
  return country.id;
}

for (const [index, lang] of (["ru", "en"] as const).entries()) {
  test.describe(`deleting a case in ${lang}`, () => {
    const t = (key: string, values: Record<string, string | number> = {}) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return Object.entries(values).reduce(
        (text, [name, value]) => text.replace(`{{${name}}}`, String(value)),
        LOCALES[lang][key],
      );
    };
    let made: string | null = null;

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
    });

    test.afterEach(async ({ page }) => {
      // A case left by a failed run would stay on the demo card.
      if (made) await page.request.delete(`/api/cases/${made}`);
      made = null;
    });

    test("a case opened twice by mistake is deleted after the question", async ({ page }) => {
      const contactId = await demoCard(page, index);
      const poland = await countryId(page, "PL");
      await page.goto(`/app/clients/${contactId}`);
      await page.locator("h1").waitFor();

      // «Создать кейсы» pressed twice while the request is on its way: one request.
      const posts: string[] = [];
      let release: () => void = () => {};
      const held = new Promise<void>((resolve) => (release = resolve));
      await page.route(`**/api/contacts/${contactId}/cases`, async (route) => {
        if (route.request().method() !== "POST") return route.fallback();
        posts.push(route.request().url());
        await held;
        await route.fallback();
      });
      await page.getByRole("button", { name: t("cases.add"), exact: true }).click();
      const form = page.locator("form").filter({
        has: page.getByRole("heading", { name: t("cases.newTitle"), exact: true }),
      });
      await form.getByLabel(t("cases.country"), { exact: true }).selectOption(poland);
      const create = form.getByRole("button", { name: t("cases.create", { count: 1 }) });
      await create.click();
      await expect(create).toBeDisabled();
      await create.click({ force: true });
      await form.getByLabel(t("cases.country"), { exact: true }).press("Enter");
      const answer = page.waitForResponse(
        (r) => r.url().endsWith(`/api/contacts/${contactId}/cases`) && r.status() === 201,
      );
      release();
      const [opened] = (await (await answer).json()) as { id: string; number: number }[];
      made = opened.id;
      await expect(form).toHaveCount(0);
      expect(posts).toHaveLength(1);

      const panel = page.locator("article.case-panel").filter({
        has: page.getByRole("heading", { name: t("cases.number", { number: opened.number }) }),
      });
      const remove = panel.getByRole("button", { name: t("cases.delete"), exact: true });
      const ask = page.getByRole("dialog");

      // «Отмена» keeps the case.
      await remove.click();
      await expect(ask).toContainText(t("cases.number", { number: opened.number }));
      await ask.getByRole("button", { name: t("common.cancel"), exact: true }).click();
      await expect(ask).toHaveCount(0);
      await expect(panel).toHaveCount(1);

      // The red button deletes it.
      await remove.click();
      await ask.getByRole("button", { name: t("cases.delete"), exact: true }).click();
      await expect(panel).toHaveCount(0);
      expect((await page.request.get(`/api/cases/${opened.id}`)).status()).toBe(404);
      made = null;
    });
  });
}

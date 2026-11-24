import { expect, test, type APIRequestContext } from "@playwright/test";
import { readFileSync } from "node:fs";

// Backlog 1.15.14: a new entry of the Lists goes to the end of its list. Once an entry
// stood at the highest order (999), the page asked for 1000 and the specialist got a bare
// «check the fields» instead of the entry. Both languages; visa purposes and vault systems.
// Entries of these lists cannot be deleted: each run switches its own ones off, so the
// demo's forms do not offer them.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

const TOP_ORDER = 999;

type Entry = { id: string; name: string; sort_order: number; is_active: boolean };
type List = { path: string; extra: Record<string, string> };

const PURPOSES: List = { path: "/api/dictionaries/purposes", extra: {} };
const SYSTEMS: List = { path: "/api/dictionaries/vault-systems", extra: { kind: "other" } };

async function entries(request: APIRequestContext, list: List): Promise<Entry[]> {
  const response = await request.get(list.path);
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as Entry[];
}

/** This worker's switched-off entry at the highest order; kept from run to run. */
async function holdTop(request: APIRequestContext, list: List, name: string): Promise<void> {
  let entry = (await entries(request, list)).find((e) => e.name === name);
  if (!entry) {
    const created = await request.post(list.path, { data: { name, ...list.extra } });
    expect(created.ok(), await created.text()).toBe(true);
    entry = (await created.json()) as Entry;
  }
  const raised = await request.patch(`${list.path}/${entry.id}`, {
    data: { sort_order: TOP_ORDER, is_active: false },
  });
  expect(raised.ok(), await raised.text()).toBe(true);
}

async function switchOff(request: APIRequestContext, list: List, name: string): Promise<void> {
  for (const entry of (await entries(request, list)).filter((e) => e.name === name)) {
    const off = await request.patch(`${list.path}/${entry.id}`, { data: { is_active: false } });
    expect(off.ok(), await off.text()).toBe(true);
  }
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`list order in ${lang}`, () => {
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };
    // The worker's slot keeps copies of the test running at once apart.
    const slot = () => `${lang} [${test.info().parallelIndex}]`;
    // The entry the page adds; new on every run, as it cannot be deleted afterwards.
    let added: { list: List; name: string } | null = null;

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
    });

    test.afterEach(async ({ page }) => {
      if (added) await switchOff(page.request, added.list, added.name);
      added = null;
    });

    test("a purpose is added after one at the highest order", async ({ page }) => {
      await holdTop(page.request, PURPOSES, `E2E top purpose ${slot()}`);
      added = { list: PURPOSES, name: `E2E purpose ${slot()} ${Date.now()}` };

      await page.goto("/app/dictionaries");
      const name = page.getByLabel(t("dict.new.purposes"), { exact: true });
      const form = page.locator("form").filter({ has: name });
      await name.fill(added.name);
      await form.getByRole("button", { name: t("dict.add"), exact: true }).click();

      await expect(page.getByText(added.name, { exact: true })).toBeVisible();
      await expect(name).toHaveValue("");
      await expect(page.locator(".alert-error")).toHaveCount(0);
      const saved = (await entries(page.request, PURPOSES)).find((e) => e.name === added?.name);
      expect(saved?.sort_order, "it stays within the highest order").toBe(TOP_ORDER);
    });

    test("a vault system is added after one at the highest order", async ({ page }) => {
      await holdTop(page.request, SYSTEMS, `E2E top system ${slot()}`);
      added = { list: SYSTEMS, name: `E2E system ${slot()} ${Date.now()}` };

      await page.goto("/app/dictionaries");
      await page.getByRole("tab", { name: t("dict.tab.vaultSystems"), exact: true }).click();
      await page.getByRole("button", { name: t("dict.addSystem"), exact: true }).click();
      const form = page.locator("form").filter({
        has: page.getByRole("heading", { name: t("dict.newSystem"), exact: true }),
      });
      await form.getByLabel(t("dict.name"), { exact: true }).fill(added.name);
      await form.getByRole("button", { name: t("common.save"), exact: true }).click();

      await expect(form).toBeHidden();
      await expect(page.getByRole("row").filter({ hasText: added.name })).toBeVisible();
      await expect(page.locator(".alert-error")).toHaveCount(0);
    });
  });
}

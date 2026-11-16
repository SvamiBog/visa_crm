import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The specialist deletes a registrar she no longer works with: one no case names and no
// payment went to is removed; one with payments is offered to be switched off instead.
// Both languages; each test takes its own registrar, as the tests run in parallel.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

const UNUSED: Record<Lang, string> = { ru: "E2E Регистратор без кейсов", en: "E2E Unused registrar" };
const PAID: Record<Lang, string> = { ru: "E2E Регистратор с платежом", en: "E2E Paid registrar" };

type Registrar = { id: string; name: string };
type Ledger = { lines: { kind: "payment" | "talon"; payment_id: string | null }[] };

/** Registrars left by an earlier run on the same stand go first, with their payments. */
async function removeRegistrars(request: APIRequestContext, names: string[]): Promise<void> {
  const list = (await (await request.get("/api/dictionaries/registrars")).json()) as Registrar[];
  for (const registrar of list.filter((r) => names.includes(r.name))) {
    const ledger = (await (
      await request.get(`/api/registrars/${registrar.id}/ledger`)
    ).json()) as Ledger;
    for (const line of ledger.lines.filter((l) => l.kind === "payment")) {
      const removed = await request.delete(`/api/registrar-payments/${line.payment_id}`);
      expect(removed.ok()).toBe(true);
    }
    expect((await request.delete(`/api/dictionaries/registrars/${registrar.id}`)).ok()).toBe(true);
  }
}

async function addRegistrar(request: APIRequestContext, name: string): Promise<Registrar> {
  const created = await request.post("/api/dictionaries/registrars", {
    data: { name, kind: "individual" },
  });
  expect(created.ok(), await created.text()).toBe(true);
  return (await created.json()) as Registrar;
}

/** Every confirm on the way is answered «yes». */
function acceptDialogs(page: Page, seen: string[]): void {
  page.on("dialog", (dialog) => {
    seen.push(dialog.message());
    void dialog.accept();
  });
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`registrars in ${lang}`, () => {
    const t = (key: string, values: Record<string, string | number> = {}) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return Object.entries(values).reduce(
        (text, [name, value]) => text.replace(`{{${name}}}`, String(value)),
        LOCALES[lang][key],
      );
    };
    // The tests run in parallel, so each one clears only the registrar it made.
    let own = "";

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
    });

    test.afterEach(async ({ page }) => {
      if (own) await removeRegistrars(page.request, [own]);
    });

    /** A fresh registrar of this test, after one left by an earlier run on the stand. The
     * worker's slot in the name keeps copies of a test running at once apart. */
    async function addOwn(request: APIRequestContext, base: string): Promise<Registrar> {
      own = `${base} [${test.info().parallelIndex}]`;
      await removeRegistrars(request, [own]);
      return addRegistrar(request, own);
    }

    async function openRegistrars(page: Page): Promise<void> {
      await page.goto("/app/dictionaries");
      await page.getByRole("tab", { name: t("dict.tab.registrars"), exact: true }).click();
    }

    test("an unused registrar is deleted from its row", async ({ page }) => {
      const { name } = await addOwn(page.request, UNUSED[lang]);
      const seen: string[] = [];
      acceptDialogs(page, seen);

      await openRegistrars(page);
      const row = page.getByRole("row").filter({ hasText: name });
      await row.getByRole("button", { name: t("dict.deleteRegistrarFor", { name }) }).click();

      await expect(row).toHaveCount(0);
      expect(seen).toEqual([t("dict.confirmDeleteRegistrar", { name })]);
    });

    test("a registrar with payments is switched off instead", async ({ page }) => {
      const registrar = await addOwn(page.request, PAID[lang]);
      const { name } = registrar;
      const paid = await page.request.post(`/api/registrars/${registrar.id}/payments`, {
        data: { paid_on: "2026-05-01", amount: "100", method: "cash" },
      });
      expect(paid.ok(), await paid.text()).toBe(true);
      const seen: string[] = [];
      acceptDialogs(page, seen);

      await openRegistrars(page);
      const row = page.getByRole("row").filter({ hasText: name });
      const remove = row.getByRole("button", { name: t("dict.deleteRegistrarFor", { name }) });
      await expect(row).toContainText(t("reference.on"));
      await remove.click();

      await expect(row).toContainText(t("reference.off"));
      expect(seen).toEqual([
        t("dict.confirmDeleteRegistrar", { name }),
        t("dict.registrarInUseSwitchOff"),
      ]);
      await expect(page.getByRole("alert")).toHaveCount(0);

      // Already off, it can only stay: she is told why, and the note goes once she opens
      // the form, which deletes on its own.
      await remove.click();
      await expect(page.getByRole("alert")).toHaveText(t("errors.registrar_in_use"));
      await row.getByRole("button", { name: t("reference.edit"), exact: true }).click();
      await expect(page.locator("form.form-card h2")).toHaveText(name);
      await expect(page.getByRole("alert")).toHaveCount(0);
    });
  });
}

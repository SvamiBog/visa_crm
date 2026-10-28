import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

// «Импорт таблиц» (backlog 1.21): the template and the prompt for the AI, then the check of
// the file it makes. The downloaded template itself is checked: nothing would change and
// nothing is written, so the demo data stays as it is. Both languages, a wide screen and a
// phone, where the page must not scroll sideways.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};
// The first line of the format's list of sheets in the prompt (app/core/prompts/).
const CLIENTS_LINE: Record<Lang, RegExp> = {
  ru: /Клиенты: Код клиента\* \| Фамилия/,
  en: /Clients: Client code\* \| Last name/,
};
const WIDTHS = [1280, 390];

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

for (const lang of ["ru", "en"] as const) {
  test.describe(`import in ${lang}`, () => {
    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
    });

    for (const width of WIDTHS) {
      test(`the template and the prompt, then the check of a file at ${width}px`, async ({
        page,
      }) => {
        const t = (key: string) => {
          if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
          return LOCALES[lang][key];
        };
        await page.setViewportSize({ width, height: 1000 });
        await page.goto("/app/import");
        await expect(page.getByRole("heading", { level: 1, name: t("import.title") })).toBeVisible();

        await page.getByRole("button", { name: t("import.showPrompt"), exact: true }).click();
        await expect(page.getByRole("textbox", { name: t("import.promptLabel") })).toHaveValue(
          CLIENTS_LINE[lang],
        );

        const downloading = page.waitForEvent("download");
        await page.getByRole("button", { name: t("import.downloadTemplate"), exact: true }).click();
        const download = await downloading;
        expect(download.suggestedFilename()).toBe(t("import.templateFile"));

        await page.getByLabel(t("import.file")).setInputFiles(await download.path());
        await expect(page.getByRole("heading", { name: t("import.stepCheck") })).toBeVisible();
        await expect(page.getByText(t("import.nothingChanges"))).toBeVisible();

        const sideways = await page.evaluate(
          () => document.documentElement.scrollWidth - window.innerWidth,
        );
        expect(sideways, "the page scrolls sideways").toBeLessThanOrEqual(0);
      });
    }
  });
}

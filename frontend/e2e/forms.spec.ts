import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// Forms line up their fields: in a row of a form grid, every field's control starts on
// the same line, whatever the length of its label. A label that wraps onto two lines
// («Личный (идентификационный) номер») must not push its control below its neighbours'.
// Checked in both languages and at the widths where the grid has 4, 3, 2 and 1 columns.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

const WIDTHS = [1440, 1280, 1024, 820, 390];

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Form = {
  name: string;
  // Opens the form; `t` gives the interface's words in the test's language.
  open: (page: Page, t: (key: string) => string) => Promise<void>;
};

async function openCard(page: Page): Promise<void> {
  const found = await page.request.post("/api/contacts/search", { data: {} });
  expect(found.ok()).toBe(true);
  const [first] = (await found.json()).items as { id: string }[];
  await page.goto(`/app/clients/${first.id}`);
  await page.locator("h1").waitFor();
}

function card(page: Page, title: string) {
  return page
    .locator("section.card")
    .filter({ has: page.getByRole("heading", { name: title, exact: true }) });
}

const FORMS: Form[] = [
  { name: "new client", open: async (page) => void (await page.goto("/app/clients?new=1")) },
  {
    name: "client's details",
    open: async (page, t) => {
      await openCard(page);
      await page.locator(".page-header").getByRole("button", { name: t("reference.edit") }).click();
    },
  },
  {
    name: "questionnaire",
    open: async (page, t) => {
      await openCard(page);
      await card(page, t("contacts.questionnaire"))
        .getByRole("button", { name: t("reference.edit"), exact: true })
        .click();
    },
  },
  {
    name: "new consultation",
    open: async (page, t) => {
      await openCard(page);
      await page.getByRole("button", { name: t("contacts.addConsultation"), exact: true }).click();
    },
  },
  {
    name: "new case",
    open: async (page, t) => {
      await openCard(page);
      await page.getByRole("button", { name: t("cases.add"), exact: true }).click();
    },
  },
  {
    name: "vault account",
    open: async (page, t) => {
      await openCard(page);
      await card(page, t("vault.title"))
        .getByRole("button", { name: t("vault.add"), exact: true })
        .click();
    },
  },
  {
    name: "trip",
    open: async (page, t) => {
      await openCard(page);
      await card(page, t("stay.cardTitle")).getByRole("button").first().click();
    },
  },
  { name: "stay calculator", open: async (page) => void (await page.goto("/app/stay-calculator")) },
];

/** Fields of a form grid whose control is not on the line of the row's other controls. */
async function misaligned(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const found: string[] = [];
    for (const grid of document.querySelectorAll(".form-grid")) {
      // Fields that start on the same line are one row of the grid.
      const rows = new Map<number, { label: string; top: number }[]>();
      for (const field of grid.children) {
        const control = field.querySelector("input, select, textarea");
        const box = field.getBoundingClientRect();
        if (!field.classList.contains("field") || !control || box.height === 0) continue;
        const row = Math.round(box.top);
        rows.set(row, [
          ...(rows.get(row) ?? []),
          {
            label: (field.firstElementChild?.textContent ?? "").trim(),
            top: Math.round(control.getBoundingClientRect().top),
          },
        ]);
      }
      for (const fields of rows.values()) {
        const line = Math.min(...fields.map((f) => f.top));
        for (const f of fields.filter((f) => f.top - line > 1)) {
          found.push(`«${f.label}»: ${f.top - line}px below the row`);
        }
      }
    }
    return found;
  });
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`forms in ${lang}`, () => {
    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
    });

    for (const form of FORMS) {
      test(`${form.name}: fields of a row line up`, async ({ page }) => {
        const t = (key: string) => {
          if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
          return LOCALES[lang][key];
        };
        await page.setViewportSize({ width: WIDTHS[0], height: 1000 });
        await form.open(page, t);
        await expect(page.locator(".form-grid .field").first()).toBeVisible();
        for (const width of WIDTHS) {
          await page.setViewportSize({ width, height: 1000 });
          expect(await misaligned(page), `${width}px wide`).toEqual([]);
        }
      });
    }
  });
}

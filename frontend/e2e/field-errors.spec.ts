import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// A mistake in one field is shown under that field and tied to it (UI audit 2026-11-17):
// the message sits right under the control, the control is aria-invalid and described
// by the message, and after the failed submit the focus is on it. No alert of the form
// repeats it. Checked in both languages, on a phone and on a desktop. None of the
// submits saves anything: each one is turned down.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

const WIDTHS = [390, 1280];

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";
// Must match MIN_PASSWORD_LENGTH in backend/app/core/security.py.
const MIN_PASSWORD_LENGTH = 10;

type Found = { id: string; phone: string | null };

async function signIn(page: Page): Promise<void> {
  expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
  const login = await page.request.post("/api/auth/login", {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
}

/** The demo's own cards, oldest first: not the ones other tests make and delete meanwhile. */
async function demoCards(page: Page): Promise<Found[]> {
  const found = await page.request.post("/api/contacts/search", { data: {} });
  expect(found.ok()).toBe(true);
  return ((await found.json()).items as Found[]).reverse();
}

async function cardWithCase(page: Page): Promise<string> {
  for (const { id } of await demoCards(page)) {
    const card = (await (await page.request.get(`/api/contacts/${id}`)).json()) as {
      cases: unknown[] | null;
    };
    if (card.cases?.length) return id;
  }
  throw new Error("The demo has no card with a case");
}

/** The message is right under the control, tied to it, and the focus is on the control. */
async function expectFieldError(form: Locator, control: Locator, text: string): Promise<void> {
  await expect(control).toHaveAttribute("aria-invalid", "true");
  await expect(control).toBeFocused();
  const [first, ...rest] = ((await control.getAttribute("aria-describedby")) ?? "").split(" ");
  expect(first, "the control is described by its message first").toBeTruthy();
  const message = form.page().locator(`[id="${first}"]`);
  await expect(message).toContainText(text);
  await expect(message).toBeVisible();
  const c = (await control.boundingBox())!;
  const m = (await message.boundingBox())!;
  const gap = m.y - (c.y + c.height);
  expect(gap, "the message starts under the control").toBeGreaterThanOrEqual(-1);
  expect(gap, "the message is right under the control").toBeLessThan(16);
  expect(Math.abs(m.x - c.x), "the message lines up with the control").toBeLessThanOrEqual(2);
  // The field's hint follows the message instead of lying over it.
  for (const id of rest) {
    const hint = (await form.page().locator(`[id="${id}"]`).boundingBox())!;
    expect(hint.y, "the hint is under the message").toBeGreaterThanOrEqual(m.y + m.height - 1);
  }
  await expect(form.getByRole("alert"), "no alert of the form repeats it").toHaveCount(0);
}

for (const lang of ["ru", "en"] as const) {
  const t = (key: string, count?: number) => {
    if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
    return LOCALES[lang][key].replace("{{count}}", String(count ?? ""));
  };

  for (const width of WIDTHS) {
    test.describe(`field errors in ${lang}, ${width}px`, () => {
      test.beforeEach(async ({ page }) => {
        await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
        await page.setViewportSize({ width, height: 900 });
      });

      test("new client: a phone another card has", async ({ page }) => {
        await signIn(page);
        const taken = (await demoCards(page)).find((c) => c.phone)?.phone;
        if (!taken) throw new Error("The demo has no card with a phone");
        await page.goto("/app/clients?new=1");
        const form = page.locator("form").filter({
          has: page.getByRole("heading", { name: t("contacts.newTitle"), exact: true }),
        });
        await form.getByLabel(t("contacts.lastName"), { exact: true }).fill("Field Error Test");
        const phone = form.getByLabel(t("contacts.phone"), { exact: true });
        await phone.fill(taken);
        await form.getByRole("button", { name: t("contacts.create"), exact: true }).click();
        await expectFieldError(form, phone, t("errors.phone_taken"));
        // The way out stays next to the message: the card that has the number.
        await expect(
          form.getByRole("link", { name: t("contacts.openExisting"), exact: true }),
        ).toBeVisible();
      });

      test("new client: a phone too short", async ({ page }) => {
        await signIn(page);
        await page.goto("/app/clients?new=1");
        const form = page.locator("form").filter({
          has: page.getByRole("heading", { name: t("contacts.newTitle"), exact: true }),
        });
        await form.getByLabel(t("contacts.lastName"), { exact: true }).fill("Field Error Test");
        const phone = form.getByLabel(t("contacts.phone"), { exact: true });
        await phone.fill("12");
        await form.getByRole("button", { name: t("contacts.create"), exact: true }).click();
        await expectFieldError(form, phone, t("errors.phone_invalid"));
        // The reader hears the message first, then the field's hint on how to type it.
        await expect(phone).toHaveAccessibleDescription(
          `${t("errors.phone_invalid")} ${t("contacts.phoneHint")}`,
        );
      });

      test("payment: an amount of zero", async ({ page }) => {
        await signIn(page);
        await page.goto(`/app/clients/${await cardWithCase(page)}`);
        await page.getByRole("button", { name: t("finance.addPayment"), exact: true }).first().click();
        const form = page.locator("form.finance-form");
        const amount = form.getByLabel(t("finance.amount"), { exact: true });
        await amount.fill("0");
        await form.getByRole("button", { name: t("common.save"), exact: true }).click();
        await expectFieldError(form, amount, t("errors.field_invalid"));
      });

      test("new case: the visa country is the submission country", async ({ page }) => {
        await signIn(page);
        const countries = (await (await page.request.get("/api/reference/countries")).json()) as {
          id: string;
          code: string;
        }[];
        const belarus = countries.find((c) => c.code === "BY")!.id;
        const [first] = await demoCards(page);
        await page.goto(`/app/clients/${first.id}`);
        await page.getByRole("button", { name: t("cases.add"), exact: true }).click();
        const form = page.locator("form").filter({
          has: page.getByRole("heading", { name: t("cases.newTitle"), exact: true }),
        });
        const submission = form.getByRole("combobox", {
          name: t("cases.submissionCountry"),
          exact: true,
        });
        await expect(submission).toHaveValue(belarus);
        await form
          .getByRole("combobox", { name: t("cases.country"), exact: true })
          .selectOption(belarus);
        await form.locator("button[type=submit]").click();
        await expectFieldError(form, submission, t("errors.same_country"));
      });

      test("login: a wrong email or password", async ({ page }) => {
        // An address with no account: the demo account's failed logins stay at zero.
        await page.goto("/login");
        const form = page.locator("form");
        await form.getByLabel(t("auth.email"), { exact: true }).fill("nobody@demo.visacrm.test");
        const password = form.getByLabel(t("auth.password"), { exact: true });
        await password.fill("not-the-password");
        await form.getByRole("button", { name: t("auth.signIn"), exact: true }).click();
        await expectFieldError(form, password, t("errors.invalid_credentials"));
      });

      test("change password: a short one and one repeated wrong", async ({ page }) => {
        await signIn(page);
        await page.goto("/change-password");
        const form = page.locator("form");
        await form.getByLabel(t("auth.currentPassword"), { exact: true }).fill(PASSWORD);
        const next = form.getByLabel(t("auth.newPassword"), { exact: true });
        const repeat = form.getByLabel(t("auth.repeatPassword"), { exact: true });
        const save = form.getByRole("button", { name: t("auth.savePassword"), exact: true });

        await next.fill("short");
        await repeat.fill("short");
        await save.click();
        await expectFieldError(
          form,
          next,
          t("errors.password_too_short", MIN_PASSWORD_LENGTH),
        );

        await next.fill("Long-enough-1");
        await repeat.fill("Long-enough-2");
        await save.click();
        await expectFieldError(form, repeat, t("errors.passwords_differ"));
        await expect(next).not.toHaveAttribute("aria-invalid", "true");
      });
    });
  }
}

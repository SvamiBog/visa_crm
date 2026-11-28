import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// Every client's card is the one route /app/clients/:id, so going from card A to card B
// inside the app (the «Open card» link of a taken phone, a link to a family member, a
// reminder, «back» and «forward») keeps the page and only changes the id. Nothing typed
// on A may come along (backlog A10, frontend audit H1): before, the open edit form of A
// stayed under B's heading with A's values and «Save» wrote them into B, and a note
// typed on A waited in B's note field. Now the whole card is made anew for the new id.
// Both languages; the test makes its own two cards and deletes them.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Card = { id: string; first_name: string; last_name: string; phone: string };

/** A mobile number nobody else has: +375 29 and seven random digits. */
function freshPhone(): string {
  return `+37529${String(Math.floor(Math.random() * 1e7)).padStart(7, "0")}`;
}

async function makeCard(page: Page, lastName: string): Promise<Card> {
  const created = await page.request.post("/api/contacts", {
    data: { first_name: `E2E ${Date.now()}`, last_name: lastName, phone: freshPhone() },
  });
  expect(created.ok(), await created.text()).toBe(true);
  return (await created.json()) as Card;
}

/** The card's name and phone as the API has them now. */
async function stored(page: Page, id: string) {
  const response = await page.request.get(`/api/contacts/${id}`);
  expect(response.ok(), await response.text()).toBe(true);
  const { contact } = (await response.json()) as { contact: Card };
  return { first_name: contact.first_name, last_name: contact.last_name, phone: contact.phone };
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`going to another client's card in ${lang}`, () => {
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };
    let a: Card;
    let b: Card;

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
      const run = `${lang}${test.info().parallelIndex}`;
      a = await makeCard(page, `Alpha ${run}`);
      b = await makeCard(page, `Beta ${run}`);
    });

    test.afterEach(async ({ page }) => {
      for (const card of [a, b]) if (card) await page.request.delete(`/api/contacts/${card.id}`);
    });

    const heading = (page: Page, card: Card) =>
      expect(page.getByRole("heading", { level: 1 })).toHaveText(
        `${card.last_name} ${card.first_name}`,
      );
    const editForm = (page: Page) =>
      page.locator("form").filter({ has: page.getByRole("heading", { name: t("contacts.editTitle") }) });

    /** On `from`'s edit form, the phone of `to`, «Save», then the «Open card» link of the
     * refusal: the way to another card the specialist meets while editing. */
    async function openOtherCardFromForm(page: Page, from: Card, to: Card) {
      await heading(page, from);
      await page
        .locator(".page-header")
        .getByRole("button", { name: t("reference.edit"), exact: true })
        .click();
      const form = editForm(page);
      await form.getByLabel(t("contacts.phone"), { exact: true }).fill(to.phone);
      await form.getByRole("button", { name: t("common.save"), exact: true }).click();
      await form.getByRole("link", { name: t("contacts.openExisting"), exact: true }).click();
      await heading(page, to);
    }

    test("the edit form of one card does not stay open on the other", async ({ page }) => {
      const before = { a: await stored(page, a.id), b: await stored(page, b.id) };
      await page.goto(`/app/clients/${a.id}`);
      await openOtherCardFromForm(page, a, b);

      await expect(editForm(page), "A's edit form closed on B").toHaveCount(0);
      await expect(
        page.locator(".page-header").getByRole("button", { name: t("reference.edit"), exact: true }),
      ).toBeVisible();

      // «Back» to A, already loaded once: still no form, and A's own form holds A.
      await page.goBack();
      await heading(page, a);
      await expect(editForm(page), "no edit form after «back»").toHaveCount(0);
      await page
        .locator(".page-header")
        .getByRole("button", { name: t("reference.edit"), exact: true })
        .click();
      const form = editForm(page);
      await expect(form.getByLabel(t("contacts.lastName"), { exact: true })).toHaveValue(
        a.last_name,
      );
      await expect(form.getByLabel(t("contacts.phone"), { exact: true })).toHaveValue(a.phone);
      const saved = page.waitForResponse(
        (r) => r.request().method() === "PATCH" && r.url().includes(`/api/contacts/${a.id}`),
      );
      await form.getByRole("button", { name: t("common.save"), exact: true }).click();
      expect((await saved).ok(), "A's own data saves on A").toBe(true);

      expect(await stored(page, a.id), "A keeps its own data").toEqual(before.a);
      expect(await stored(page, b.id), "B keeps its own data").toEqual(before.b);
    });

    test("a note typed on one card does not wait on the other", async ({ page }) => {
      // B first, so that it is loaded when «back» returns to it.
      await page.goto(`/app/clients/${b.id}`);
      await openOtherCardFromForm(page, b, a);
      const note = page.getByPlaceholder(t("contacts.notePlaceholder"));
      await note.fill(`Draft for ${a.last_name}`);

      await page.goBack();
      await heading(page, b);
      await expect(note, "A's note draft is not in B's note field").toHaveValue("");
      await expect(
        page.getByRole("button", { name: t("contacts.addNote"), exact: true }),
      ).toBeDisabled();
      const notes = await page.request.get(`/api/contacts/${b.id}`);
      expect(((await notes.json()) as { notes: unknown[] }).notes, "B has no notes").toEqual([]);
    });
  });
}

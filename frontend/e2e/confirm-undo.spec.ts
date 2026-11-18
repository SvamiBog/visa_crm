import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// Heavy actions ask in the site's own window, not the browser's (decisions.md →
// «Подтверждение и отмена удаления»): Escape and «Отмена» leave things as they were, the
// red button does it. A note is deleted without a question: it stays struck through with
// «Отменить», and the deletion goes to the server only when the time is up — or at once
// when the page is left. Both languages; the window on a computer and on a phone.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

// undo.ts: UNDO_MS is 7 seconds.
const PAST_UNDO_MS = 8_000;

async function noteBodies(request: APIRequestContext, contactId: string): Promise<string[]> {
  const card = await request.get(`/api/contacts/${contactId}`);
  expect(card.ok(), await card.text()).toBe(true);
  return ((await card.json()) as { notes: { body: string }[] }).notes.map((n) => n.body);
}

async function addNote(request: APIRequestContext, contactId: string): Promise<string> {
  const body = `E2E note ${Date.now()} ${test.info().parallelIndex}`;
  const added = await request.post(`/api/contacts/${contactId}/notes`, { data: { body } });
  expect(added.ok(), await added.text()).toBe(true);
  return body;
}

/** Every DELETE the page sends, as it is sent. */
function deletions(page: Page): string[] {
  const sent: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "DELETE") sent.push(new URL(request.url()).pathname);
  });
  return sent;
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`confirm and undo in ${lang}`, () => {
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };
    // Each test has a client card of its own, deleted afterwards if it is still there.
    let contactId = "";
    let name = "";

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
      name = `E2E ${lang} ${Date.now()} ${test.info().parallelIndex}`;
      const created = await page.request.post("/api/contacts", { data: { first_name: name } });
      expect(created.ok(), await created.text()).toBe(true);
      contactId = ((await created.json()) as { id: string }).id;
    });

    test.afterEach(async ({ page }) => {
      if (contactId) await page.request.delete(`/api/contacts/${contactId}`);
    });

    for (const width of [1280, 390]) {
      test(`a card is deleted only after «Delete» in the site's window, ${width}px`, async ({
        page,
      }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(`/app/clients/${contactId}`);
        const button = page.getByRole("button", { name: t("contacts.deleteCard"), exact: true });
        const ask = page.getByRole("dialog");

        await button.click();
        await expect(ask).toBeVisible();
        await expect(ask).toContainText(name);
        // The refusing button has the focus, so a stray Enter deletes nothing.
        const cancel = ask.getByRole("button", { name: t("common.cancel"), exact: true });
        await expect(cancel).toBeFocused();
        const box = await ask.boundingBox();
        expect(box!.x, "the window fits the screen").toBeGreaterThanOrEqual(0);
        expect(box!.x + box!.width).toBeLessThanOrEqual(width);

        await page.keyboard.press("Escape");
        await expect(ask).toBeHidden();
        await expect(button, "the focus goes back to the button").toBeFocused();
        expect((await page.request.get(`/api/contacts/${contactId}`)).status()).toBe(200);

        await button.click();
        await cancel.click();
        await expect(ask).toBeHidden();
        expect((await page.request.get(`/api/contacts/${contactId}`)).status()).toBe(200);

        await button.click();
        await ask.getByRole("button", { name: t("confirm.delete"), exact: true }).click();
        await expect(page).toHaveURL(/\/app\/clients$/);
        expect((await page.request.get(`/api/contacts/${contactId}`)).status()).toBe(404);
        contactId = "";
      });
    }

    test("a deleted note comes back with «Undo» and goes only when the time is up", async ({
      page,
    }) => {
      const body = await addNote(page.request, contactId);
      const sent = deletions(page);
      await page.clock.install();
      await page.goto(`/app/clients/${contactId}`);
      const note = page.getByRole("listitem").filter({ hasText: body });
      const remove = note.getByRole("button", { name: t("contacts.deleteNote"), exact: true });
      const toast = page.getByRole("status").filter({ hasText: t("undo.noteDeleted") });

      await remove.click();
      await expect(page.getByRole("dialog"), "no question: «Undo» instead").toHaveCount(0);
      await expect(toast).toBeVisible();
      await expect(note, "struck through in its place").toHaveClass(/deleting/);
      await toast.getByRole("button", { name: t("undo.undo"), exact: true }).click();
      await expect(toast).toBeHidden();
      await expect(note).not.toHaveClass(/deleting/);
      await page.clock.runFor(PAST_UNDO_MS);
      expect(sent, "an undone deletion is never sent").toEqual([]);
      expect(await noteBodies(page.request, contactId)).toContain(body);

      await remove.click();
      await expect(toast).toBeVisible();
      expect(sent, "nothing is sent while «Undo» is offered").toEqual([]);
      await page.clock.runFor(PAST_UNDO_MS);
      await expect(note).toHaveCount(0);
      await expect(toast).toBeHidden();
      expect(sent).toHaveLength(1);
      expect(await noteBodies(page.request, contactId)).not.toContain(body);
    });

    test("a note waiting for its deletion is deleted at once when the page is left", async ({
      page,
    }) => {
      const body = await addNote(page.request, contactId);
      await page.goto(`/app/clients/${contactId}`);
      const note = page.getByRole("listitem").filter({ hasText: body });
      await note.getByRole("button", { name: t("contacts.deleteNote"), exact: true }).click();
      await expect(page.getByRole("status").filter({ hasText: t("undo.noteDeleted") })).toBeVisible();

      await page.goto("about:blank");
      // Well within the 7 seconds: it went as the page closed, not by the timer.
      await expect
        .poll(() => noteBodies(page.request, contactId), { timeout: 3_000 })
        .not.toContain(body);
    });

    test("a refused deletion says why and the note stays", async ({ page }) => {
      const body = await addNote(page.request, contactId);
      await page.route("**/api/contacts/*/notes/*", (route) =>
        route.request().method() === "DELETE"
          ? route.fulfill({
              status: 404,
              contentType: "application/json",
              body: JSON.stringify({ detail: "not_found" }),
            })
          : route.fallback(),
      );
      await page.clock.install();
      await page.goto(`/app/clients/${contactId}`);
      const note = page.getByRole("listitem").filter({ hasText: body });

      await note.getByRole("button", { name: t("contacts.deleteNote"), exact: true }).click();
      await page.clock.runFor(PAST_UNDO_MS);
      const failed = page.getByRole("status").filter({ hasText: t("undo.failed") });
      await expect(failed).toBeVisible();
      await expect(failed).toContainText(t("errors.not_found"));
      await expect(note).toBeVisible();
      await expect(note).not.toHaveClass(/deleting/);
      await failed.getByRole("button", { name: t("undo.close"), exact: true }).click();
      await expect(failed).toBeHidden();
    });
  });
}

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// Whoever signs in next in a browser tab sees nothing the last account had there: not its
// client search, not the rows it found, not a card it opened (security audit run-1, N14).
// A session also ends outside the tab's own «Выйти»: signing out in another tab, a password
// change, an employee blocked. The tab left open then lands on the sign-in page the next
// time it asks who is signed in, and an employee may sign in there. The cached answers
// carry no account, so they go when the session is gone and when someone else signs in.
// Both languages.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist and the employee (python -m app.cli seed-demo); the employee is
// given every third case in work and sees only those clients.
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const EMPLOYEE_EMAIL = process.env.E2E_EMPLOYEE_EMAIL ?? "visa1.staff@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

// How long the app trusts its answer to «who is signed in» (useMe in src/auth.ts).
const ME_FRESH = "01:30";

type Row = { id: string; full_name: string; telegram: string | null };

/** Every client the account sees, through a session of its own. */
async function clientsOf(request: APIRequestContext, email: string): Promise<Row[]> {
  const login = await request.post("/api/auth/login", { data: { email, password: PASSWORD } });
  expect(login.ok(), await login.text()).toBe(true);
  const rows: Row[] = [];
  for (let offset = 0; ; offset += 50) {
    const found = await request.post("/api/contacts/search", { data: { offset } });
    expect(found.ok(), await found.text()).toBe(true);
    const page = (await found.json()) as { items: Row[]; has_more: boolean };
    rows.push(...page.items);
    if (!page.has_more) return rows;
  }
}

/** A demo client of the specialist whose name shows nowhere on the employee's screens. The
 * demo's own cards (Telegram demo_…), so no other test deletes it meanwhile. */
function hiddenClient(specialist: Row[], employee: Row[]): Row {
  const client = specialist.find(
    (row) =>
      row.telegram?.startsWith("demo_") &&
      row.full_name &&
      !employee.some((other) => other.full_name.includes(row.full_name)),
  );
  expect(client, "the demo has a client the employee does not see").toBeTruthy();
  return client!;
}

/** From now on, records each time the text shows in the page or in a field, even for a
 * moment: an old answer drawn while the new one loads counts too. */
async function watchFor(page: Page, text: string): Promise<void> {
  await page.evaluate((needle) => {
    const w = window as unknown as { seenOld: string[] };
    w.seenOld = [];
    new MutationObserver((records) => {
      const added = records.some((record) =>
        [...record.addedNodes].some((node) => node.textContent?.includes(needle)),
      );
      if (added || document.body.textContent?.includes(needle)) w.seenOld.push(location.pathname);
      for (const field of document.querySelectorAll("input")) {
        if (field.value.includes(needle)) w.seenOld.push(`${location.pathname} (field)`);
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  }, text);
}

async function seenOld(page: Page): Promise<string[]> {
  return page.evaluate(() => (window as unknown as { seenOld: string[] }).seenOld);
}

for (const lang of ["ru", "en"] as const) {
  test.describe(`session cache in ${lang}`, () => {
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };
    const menuLink = (page: Page, key: string) =>
      page
        .getByRole("navigation", { name: t("nav.label"), exact: true })
        .getByRole("link", { name: t(key), exact: true });
    const search = (page: Page) =>
      page.getByRole("searchbox", { name: t("contacts.search"), exact: true });

    let client: Row;

    test.beforeEach(async ({ page, request }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      // Time runs as usual; the test only skips the minute the tab trusts its last answer.
      await page.context().clock.install();
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const employee = await clientsOf(request, EMPLOYEE_EMAIL);
      // The browser signs in as the specialist here: page.request shares its cookies.
      client = hiddenClient(await clientsOf(page.request, EMAIL), employee);
    });

    /** The specialist looks the client up in the list, as on any working day. */
    async function findClient(page: Page): Promise<void> {
      await page.goto("/app");
      await menuLink(page, "nav.clients").click();
      await search(page).fill(client.full_name);
      await expect(page.getByRole("link", { name: client.full_name, exact: true })).toBeVisible();
    }

    /** The specialist opens the client's card from the list. */
    async function openCard(page: Page): Promise<void> {
      await page.getByRole("link", { name: client.full_name, exact: true }).click();
      await expect(page.getByRole("heading", { name: client.full_name, level: 1 })).toBeVisible();
    }

    /** The account menu (it shows who is signed in) → «Выйти». */
    async function signOut(page: Page): Promise<void> {
      await page.getByRole("button", { name: EMAIL, exact: true }).click();
      await page.getByRole("button", { name: t("auth.logout"), exact: true }).click();
      await expect(page).toHaveURL(/\/login$/);
    }

    /** Later the tab opens another page, which asks again who is signed in. */
    async function comeBackLater(page: Page): Promise<void> {
      await page.context().clock.fastForward(ME_FRESH);
      await page.getByRole("button", { name: EMAIL, exact: true }).click();
      await page.getByRole("link", { name: t("nav.account"), exact: true }).click();
    }

    /** «Назад» to the card the specialist had open: an old card would be drawn before the
     * answer comes (the card then retries a while before it shows its error). */
    async function backToCard(page: Page, steps: number): Promise<void> {
      for (let step = 1; step < steps; step++) await page.goBack();
      const card = page.waitForResponse((r) => r.url().endsWith(`/api/contacts/${client.id}`));
      await page.goBack();
      await expect(page).toHaveURL(new RegExp(`/app/clients/${client.id}$`));
      expect((await card).ok(), "the employee cannot open the card").toBe(false);
    }

    async function signInAsEmployee(page: Page): Promise<void> {
      const form = page.locator("form.auth-card");
      await form.getByLabel(t("auth.email"), { exact: true }).fill(EMPLOYEE_EMAIL);
      await form.getByLabel(t("auth.password"), { exact: true }).fill(PASSWORD);
      await form.getByRole("button", { name: t("auth.signIn"), exact: true }).click();
      await expect(page).toHaveURL(/\/app$/);
    }

    /** The employee opens «Клиенты»: an empty search and only their own clients. */
    async function expectEmployeeList(page: Page): Promise<void> {
      const loaded = page.waitForResponse((r) => r.url().endsWith("/api/contacts/search"));
      await menuLink(page, "nav.clients").click();
      expect((await loaded).ok(), "the employee's own list").toBe(true);
      await expect(search(page), "the search field of the last account is empty").toHaveValue("");
      await expect(page.getByRole("link", { name: client.full_name, exact: true })).toHaveCount(0);
    }

    test("a session ended in another tab leaves nothing for the next account", async ({
      page,
    }) => {
      await findClient(page);
      await openCard(page);

      // Another tab of the same browser signs out: the session of both tabs is gone.
      const other = await page.context().newPage();
      await other.goto("/app");
      await signOut(other);
      await other.close();

      await comeBackLater(page);
      await expect(page).toHaveURL(/\/login$/);

      await watchFor(page, client.full_name);
      await signInAsEmployee(page);
      await expectEmployeeList(page);
      // Home, then the card.
      await backToCard(page, 2);
      expect(await seenOld(page), "the last account's client never shows").toEqual([]);
    });

    test("an account signed in in another tab finds nothing of the last one", async ({
      page,
    }) => {
      await findClient(page);
      await openCard(page);

      // In another tab the specialist signs out and the employee signs in: both tabs now
      // have the employee's session, and this one has not asked yet.
      const other = await page.context().newPage();
      await other.goto("/app");
      await signOut(other);
      await signInAsEmployee(other);
      await other.close();

      await comeBackLater(page);
      await expect(page).toHaveURL(/\/settings$/);
      await expect(page.getByRole("button", { name: EMPLOYEE_EMAIL, exact: true })).toBeVisible();

      await watchFor(page, client.full_name);
      await expectEmployeeList(page);
      // The account page, then the card.
      await backToCard(page, 2);
      expect(await seenOld(page), "the last account's client never shows").toEqual([]);
    });

    test("signing out leaves nothing for the next account", async ({ page }) => {
      await findClient(page);
      await signOut(page);

      await watchFor(page, client.full_name);
      await signInAsEmployee(page);
      await expectEmployeeList(page);
      expect(await seenOld(page), "the last account's client never shows").toEqual([]);
    });
  });
}

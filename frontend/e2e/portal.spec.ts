import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The client portal (backlog 2.9): the specialist gives the client the link and a code
// from the case; the client signs in on a phone or a computer, reads the data notice once
// and uploads documents to the items of the case's checklist; the specialist accepts one
// and returns another with a reason, which the client then sees. Phone and desktop.
//
// The spec makes a card, a case and two checklist items of its own (a unique name) and
// keeps them: other specs borrow the demo's cards (search «demo_»), never these. The client
// files with a family member, so the case is no single case in work that the board's test
// could pick while this one runs; the items are switched off at the end.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

const PNG = Buffer.concat([Buffer.from("89504e470d0a1a0a", "hex"), Buffer.alloc(64)]);
const PDF = Buffer.concat([Buffer.from("%PDF-1.7\n"), Buffer.alloc(64, 120)]);

type Country = { id: string; code: string };

async function ok<T>(response: Awaited<ReturnType<Page["request"]["get"]>>): Promise<T> {
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as T;
}

/** The item on the client's page, found by its name. */
function clientItem(page: Page, name: string): Locator {
  return page.locator("li.portal-item").filter({ has: page.getByText(name, { exact: true }) });
}

/** The item in the case's package, found by its name. */
function caseItem(page: Page, name: string): Locator {
  return page.locator("li.package-item").filter({ has: page.getByText(name, { exact: true }) });
}

for (const [lang, width] of [
  ["ru", 390],
  ["en", 1280],
] as const) {
  const t = (key: string, values: Record<string, string | number> = {}) => {
    if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
    return Object.entries(values).reduce(
      (text, [name, value]) => text.replace(`{{${name}}}`, String(value)),
      LOCALES[lang][key],
    );
  };

  test(`the client uploads, the specialist accepts and returns (${lang}, ${width}px)`, async ({
    page,
    browser,
  }) => {
    expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
    await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
    await page.setViewportSize({ width, height: 900 });
    await ok(await page.request.post("/api/auth/login", { data: { email: EMAIL, password: PASSWORD } }));

    // A card, a case to Poland and two items of the specialist's own list for it.
    const stamp = `${lang}${Date.now()}`;
    const contact = await ok<{ id: string }>(
      await page.request.post("/api/contacts", { data: { last_name: "Порталов", first_name: stamp } }),
    );
    const countries = new Map(
      (await ok<Country[]>(await page.request.get("/api/reference/countries"))).map((c) => [c.code, c]),
    );
    const poland = countries.get("PL")!;
    const bank = `Выписка ${stamp}`;
    const certificate = `Справка ${stamp}`;
    const items: string[] = [];
    for (const name of [bank, certificate]) {
      const item = await ok<{ id: string }>(
        await page.request.post(`/api/checklists/${poland.id}/items`, {
          data: { name, note: name === bank ? "За 3 месяца" : null },
        }),
      );
      items.push(item.id);
    }
    const opened = await ok<{ id: string; contact_id: string }[]>(
      await page.request.post(`/api/contacts/${contact.id}/cases?allow_case_in_work=true`, {
        data: {
          destination_id: poland.id,
          submission_country_id: countries.get("BY")!.id,
          applicants: [
            { contact_id: contact.id },
            { last_name: "Порталова", first_name: stamp, label: "жена" },
          ],
        },
      }),
    );

    const caseId = opened.find((c) => c.contact_id === contact.id)!.id;

    // In the case: the link and a code to pass on.
    await page.goto(`/app/clients/${contact.id}`);
    const block = page.locator(".portal-block");
    await expect(block.getByRole("heading", { name: t("portal.title") })).toBeVisible();
    const url = await block.getByRole("textbox", { name: t("portal.link") }).inputValue();
    expect(url).toMatch(/\/u\/[A-Za-z0-9_-]{43}$/);
    await block.getByRole("button", { name: t("portal.newCode") }).click();
    const code = (await block.locator(".portal-code").textContent())?.trim() ?? "";
    expect(code).toMatch(/^\d{6}$/);

    // The client's own browser, with no CRM account.
    const visitor = await browser.newContext({ viewport: { width, height: 900 } });
    await visitor.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
    const client = await visitor.newPage();
    await client.goto(new URL(url).pathname);
    await expect(client.getByRole("heading", { name: t("clientPortal.codeTitle") })).toBeVisible();
    await client.getByLabel(t("clientPortal.code")).fill(code);
    await client.getByRole("button", { name: t("clientPortal.enter") }).click();

    // The data notice once, before the first upload.
    await expect(client.getByRole("heading", { name: t("clientPortal.noticeTitle") })).toBeVisible();
    const proceed = client.getByRole("button", { name: t("clientPortal.continue") });
    await expect(proceed).toBeDisabled();
    await client.getByLabel(t("clientPortal.noticeRead")).check();
    await proceed.click();

    for (const [name, file] of [
      [bank, { name: "bank.pdf", mimeType: "application/pdf", buffer: PDF }],
      [certificate, { name: "spravka.png", mimeType: "image/png", buffer: PNG }],
    ] as const) {
      const item = clientItem(client, name);
      await expect(item.getByText(t("clientPortal.status.to_upload"))).toBeVisible();
      await item.locator('input[type="file"]').setInputFiles(file);
      await expect(item.getByText(t("clientPortal.status.pending"))).toBeVisible();
      await expect(item.getByText(file.name)).toBeVisible();
    }
    await expect(clientItem(client, bank).getByText("За 3 месяца")).toBeVisible();
    // Nothing on the page is wider than the screen.
    const overflow = await client.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);

    // The specialist sees both files waiting: accepts one, returns the other.
    await page.reload();
    await expect(page.getByText(t("portal.waiting", { count: 2 }))).toBeVisible();
    // «Требует внимания» in the card says so too, until each file is reviewed.
    await expect(page.getByText(t("attention.kind.portal_uploads", { count: 2 }))).toBeVisible();
    await page.getByRole("button", { name: new RegExp(t("package.show", { count: 0 }).split("(")[0]) }).click();
    const bankRow = caseItem(page, bank);
    await bankRow.getByRole("button", { name: t("portal.accept") }).click();
    await bankRow.getByLabel(t("package.documentDate")).fill(new Date().toISOString().slice(0, 10));
    await bankRow.getByRole("button", { name: t("portal.accept") }).click();
    await expect(bankRow.getByText(t("portal.fileStatus.accepted"))).toBeVisible();

    const certificateRow = caseItem(page, certificate);
    await certificateRow.getByRole("button", { name: t("portal.return") }).click();
    await certificateRow.getByLabel(t("portal.returnReason")).selectOption("unreadable");
    await certificateRow.getByRole("button", { name: t("portal.returnSend") }).click();
    await expect(certificateRow.getByText(t("portal.fileStatus.returned"))).toBeVisible();
    await expect(page.getByText(t("portal.waiting", { count: 2 }))).toHaveCount(0);

    // The client sees what was accepted and why the other came back.
    await client.reload();
    await expect(clientItem(client, bank).getByText(t("clientPortal.status.accepted"))).toBeVisible();
    const returned = clientItem(client, certificate);
    await expect(returned.getByText(t("clientPortal.status.returned"))).toBeVisible();
    await expect(returned.getByText(t("portal.reason.unreadable"))).toBeVisible();
    await expect(returned.locator('input[type="file"]')).toHaveCount(1);

    // Handed over for review: the link and the code stop working for the client.
    const moved = await page.request.post("/api/cases/stage", {
      data: { case_ids: [caseId], stage: "review" },
    });
    expect(moved.ok(), await moved.text()).toBe(true);
    await client.reload();
    await expect(client.getByText(t("clientPortal.linkInvalid"))).toBeVisible();
    await page.reload();
    await expect(page.getByText(t("portal.closed"))).toBeVisible();
    await visitor.close();

    // The items leave the list again: the demo's other Polish cases keep their checklist.
    for (const id of items) {
      await ok(
        await page.request.patch(`/api/checklists/items/${id}`, { data: { is_active: false } }),
      );
    }
  });
}

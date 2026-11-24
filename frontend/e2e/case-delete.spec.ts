import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// A case opened by mistake, a duplicate (pilot fix 1, docs/decisions.md → «Пилот: правки
// визовика (2026-11-19)»): «Создать кейсы» sends one request however fast it is pressed
// and the form closes after it; «Удалить кейс» asks in the site's window, naming the
// case, and the case goes only after the red button. A case with money is refused before
// the question, naming the money: here a partner payout marked as paid. The money taken
// off in the case finances, the case goes (backlog 1.15.12): a talon taken off is not
// filled in again from the registrar's price list, and a payment deleted a moment ago,
// still offering «Отменить», does not hold the case. The list's prepayment and the
// partner's terms emptied in the finances stay empty too (owner's answer 2026-11-24).
// Both languages.

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

// Each language's own registrar, so its balance holds only this test's money.
const REGISTRAR: Record<"ru" | "en", string> = {
  ru: "E2E Регистратор удаляемого кейса",
  en: "E2E Registrar of a deleted case",
};
// A purpose of these tests, last in the list: the list's prepayment for it reaches no other
// test's case. Both languages share it and the prepayment, and leave them on the stand:
// the list is saved whole, so two tests changing it at once would undo each other.
const PURPOSE = "E2E Цель предоплаты";
// Each language's own partner.
const PARTNER: Record<"ru" | "en", string> = {
  ru: "E2E Партнёр подсказки",
  en: "E2E Suggested partner",
};

type Prepayment = {
  country_id: string;
  purpose_id: string | null;
  amount: string;
  currency: string;
};

type Ledger = {
  paid: string;
  charged: string;
  balance: string;
  talons: number;
  lines: { kind: "payment" | "talon"; payment_id: string | null }[];
};

async function ledger(request: APIRequestContext, registrarId: string): Promise<Ledger> {
  const response = await request.get(`/api/registrars/${registrarId}/ledger`);
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as Ledger;
}

/** A registrar left by an earlier run goes first, with the payments made to him. */
async function removeRegistrar(request: APIRequestContext, name: string): Promise<void> {
  const list = (await (await request.get("/api/dictionaries/registrars")).json()) as {
    id: string;
    name: string;
  }[];
  for (const registrar of list.filter((r) => r.name === name)) {
    for (const line of (await ledger(request, registrar.id)).lines) {
      if (line.kind === "payment") await request.delete(`/api/registrar-payments/${line.payment_id}`);
    }
    await request.delete(`/api/dictionaries/registrars/${registrar.id}`);
  }
}

/** The purpose of these tests: made once, by whichever test comes first. */
async function testPurpose(request: APIRequestContext): Promise<string> {
  const find = async () =>
    ((await (await request.get("/api/dictionaries/purposes")).json()) as {
      id: string;
      name: string;
    }[]).find((p) => p.name === PURPOSE);
  const found = await find();
  if (found) return found.id;
  const created = await request.post("/api/dictionaries/purposes", {
    // Not the highest order allowed: the Lists page numbers a new purpose after the last.
    data: { name: PURPOSE, sort_order: 500 },
  });
  if (created.ok()) return ((await created.json()) as { id: string }).id;
  // Made by the other language's test a moment ago.
  const made = await find();
  expect(made, await created.text()).toBeTruthy();
  return made!.id;
}

/** A case of a failed run: its money taken off first, as the site asks. */
async function removeCase(request: APIRequestContext, caseId: string): Promise<void> {
  const finance = await request.get(`/api/cases/${caseId}/finance`);
  if (finance.ok()) {
    for (const p of ((await finance.json()) as { payments: { id: string }[] }).payments) {
      await request.delete(`/api/payments/${p.id}`);
    }
    await request.patch(`/api/cases/${caseId}/finance`, {
      data: { partner_paid_on: null, registrar_fee_amount: null },
    });
  }
  await request.delete(`/api/cases/${caseId}`);
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
      // A case left by a failed run would stay on the demo card; its money holds it, so
      // the money goes first.
      if (made) await removeCase(page.request, made);
      made = null;
    });

    /** A demo card's new case in Poland, with whatever the test gives it. */
    async function openCase(
      page: Page,
      contactId: string,
      fields: Record<string, unknown> = {},
    ): Promise<{ id: string; number: number }> {
      const opened = await page.request.post(
        `/api/contacts/${contactId}/cases?allow_case_in_work=true`,
        {
          data: {
            destination_id: await countryId(page, "PL"),
            submission_country_id: await countryId(page, "BY"),
            applicants: [{ contact_id: contactId }],
            ...fields,
          },
        },
      );
      expect(opened.ok(), await opened.text()).toBe(true);
      const [kept] = (await opened.json()) as { id: string; number: number }[];
      made = kept.id;
      return kept;
    }

    function casePanel(page: Page, number: number) {
      return page.locator("article.case-panel").filter({
        has: page.getByRole("heading", { name: t("cases.number", { number }) }),
      });
    }

    test("a talon taken off in the finances stays off and the case goes", async ({ page }) => {
      // The refusal names the talon and the paid payout and says how to take them off.
      // The talon is taken off first, the payout's date on the next save: the talon is
      // not filled in again from the price list, and the case goes. The registrar was
      // paid for the talon: his payment stays, as an advance.
      await removeRegistrar(page.request, REGISTRAR[lang]);
      const created = await page.request.post("/api/dictionaries/registrars", {
        data: { name: REGISTRAR[lang], kind: "individual", currency: "EUR" },
      });
      expect(created.ok(), await created.text()).toBe(true);
      const registrar = (await created.json()) as { id: string };
      const priced = await page.request.put(`/api/dictionaries/registrars/${registrar.id}/prices`, {
        data: [
          {
            country_id: await countryId(page, "PL"),
            purpose_id: null,
            hall: "standard",
            amount: "40",
            currency: "EUR",
          },
        ],
      });
      expect(priced.ok(), await priced.text()).toBe(true);
      const contactId = await demoCard(page, 4 + index);
      const kept = await openCase(page, contactId, { registrar_id: registrar.id });
      const paid = await page.request.post(`/api/registrars/${registrar.id}/payments`, {
        data: { paid_on: "2026-06-01", amount: "40", method: "cash", talons: 1 },
      });
      expect(paid.ok(), await paid.text()).toBe(true);
      // «Списать» in the settlements: the talon of the price list.
      const charged = await page.request.post(`/api/registrars/${registrar.id}/talons`, {
        data: { case_id: kept.id },
      });
      expect(charged.ok(), await charged.text()).toBe(true);
      const partner = await page.request.patch(`/api/cases/${kept.id}/finance`, {
        data: {
          price: "500",
          partner_terms: "fixed",
          partner_fixed: "50",
          partner_paid_on: "2026-06-01",
        },
      });
      expect(partner.ok(), await partner.text()).toBe(true);

      await page.goto(`/app/clients/${contactId}`, { waitUntil: "networkidle" });
      const panel = casePanel(page, kept.number);
      const remove = panel.getByRole("button", { name: t("cases.delete"), exact: true });
      await remove.click();
      const refusal = panel.getByRole("alert");
      await expect(refusal).toContainText(t("cases.keptBy.registrar_fee"));
      await expect(refusal).toContainText(t("cases.keptBy.partner_paid"));
      await expect(page.getByRole("dialog")).toHaveCount(0);

      // The talon's amount emptied: the form says where it goes, and it goes.
      const finance = panel.locator("section.finance");
      const edit = finance
        .locator(".card-head")
        .getByRole("button", { name: t("reference.edit"), exact: true });
      const form = finance.locator("form.finance-form");
      const talon = form.getByRole("group", { name: t("finance.talonField"), exact: true });
      const amount = talon.getByLabel(t("finance.amount"), { exact: true });
      await edit.click();
      await expect(amount).toHaveValue("40.00");
      await amount.fill("");
      await expect(talon.getByRole("status")).toHaveText(t("finance.talonOff"));
      await form.getByRole("button", { name: t("common.save"), exact: true }).click();
      await expect(form).toHaveCount(0);
      const after = await ledger(page.request, registrar.id);
      expect([after.paid, after.charged, after.balance]).toEqual(["40.00", "0.00", "40.00"]);

      // Opened again, the talon stays empty; the payout's date cleared, it stays off.
      await edit.click();
      await expect(amount).toHaveValue("");
      await form.getByLabel(t("finance.partnerPaidOn"), { exact: true }).fill("");
      await form.getByRole("button", { name: t("common.save"), exact: true }).click();
      await expect(form).toHaveCount(0);

      await remove.click();
      const ask = page.getByRole("dialog");
      await expect(ask).toContainText(t("cases.deletedWith.terms"));
      await ask.getByRole("button", { name: t("cases.delete"), exact: true }).click();
      await expect(panel).toHaveCount(0);
      expect((await page.request.get(`/api/cases/${kept.id}`)).status()).toBe(404);
      made = null;
      const left = await ledger(page.request, registrar.id);
      expect([left.balance, left.talons]).toEqual(["40.00", 0]);
      await removeRegistrar(page.request, REGISTRAR[lang]);
    });

    test("a prepayment and partner terms emptied in the finances stay empty", async ({
      page,
    }) => {
      // The form fills the list's prepayment and the partner's terms in. Emptied and saved,
      // they are not filled in again when the form opens next, and a save leaves them out.
      const request = page.request;
      const poland = await countryId(page, "PL");
      const purposeId = await testPurpose(request);
      const listed = (await (await request.get("/api/dictionaries/prepayments")).json()) as
        Prepayment[];
      if (!listed.some((p) => p.purpose_id === purposeId && p.country_id === poland)) {
        // The other language's test may save the same list at the same time: the same
        // list with the same row, whichever is last.
        const list = await request.put("/api/dictionaries/prepayments", {
          data: [
            ...listed.map(({ country_id, purpose_id, amount, currency }) => ({
              country_id,
              purpose_id,
              amount,
              currency,
            })),
            { country_id: poland, purpose_id: purposeId, amount: "150" },
          ],
        });
        expect(list.ok(), await list.text()).toBe(true);
      }
      const partners = (await (await request.get("/api/dictionaries/partners")).json()) as {
        id: string;
        name: string;
      }[];
      for (const old of partners.filter((p) => p.name === PARTNER[lang])) {
        await request.delete(`/api/dictionaries/partners/${old.id}`);
      }
      const added = await request.post("/api/dictionaries/partners", {
        data: { name: PARTNER[lang] },
      });
      expect(added.ok(), await added.text()).toBe(true);
      const partner = (await added.json()) as { id: string };
      const terms = await request.put(`/api/dictionaries/partners/${partner.id}/terms`, {
        data: [{ country_id: null, terms_type: "percent", percent: "10" }],
      });
      expect(terms.ok(), await terms.text()).toBe(true);
      const contactId = await demoCard(page, 8 + index);
      const card = await request.get(`/api/contacts/${contactId}`);
      expect(card.ok(), await card.text()).toBe(true);
      const { contact } = (await card.json()) as { contact: { partner_id: string | null } };
      const linked = await request.patch(`/api/contacts/${contactId}`, {
        data: { partner_id: partner.id },
      });
      expect(linked.ok(), await linked.text()).toBe(true);
      try {
        const kept = await openCase(page, contactId, { purpose_id: purposeId });
        const { base_currency: base } = (await (await request.get("/api/workspace")).json()) as {
          base_currency: string;
        };

        await page.goto(`/app/clients/${contactId}`, { waitUntil: "networkidle" });
        const finance = casePanel(page, kept.number).locator("section.finance");
        const edit = finance
          .locator(".card-head")
          .getByRole("button", { name: t("reference.edit"), exact: true });
        const form = finance.locator("form.finance-form");
        const prepayment = form.getByLabel(t("finance.prepaymentField", { currency: base }), {
          exact: true,
        });
        const partnerTerms = form.getByRole("combobox", {
          name: t("finance.partnerTerms"),
          exact: true,
        });
        const save = form.getByRole("button", { name: t("common.save"), exact: true });
        await edit.click();
        await expect(prepayment).toHaveValue("150.00");
        await expect(partnerTerms).toHaveValue("percent");
        await form
          .getByLabel(t("finance.priceField", { currency: base }), { exact: true })
          .fill("500");
        await prepayment.fill("");
        await partnerTerms.selectOption("");
        await save.click();
        await expect(form).toHaveCount(0);

        // Opened again they stay empty, and saved as they are, they do not come back.
        await edit.click();
        await expect(prepayment).toHaveValue("");
        await expect(partnerTerms).toHaveValue("");
        await save.click();
        await expect(form).toHaveCount(0);
        await edit.click();
        await expect(prepayment).toHaveValue("");
        await expect(partnerTerms).toHaveValue("");
        await form.getByRole("button", { name: t("common.cancel"), exact: true }).click();
        const saved = (await (await request.get(`/api/cases/${kept.id}/finance`)).json()) as {
          price: string;
          prepayment: string | null;
          partner_terms: string | null;
        };
        expect([saved.price, saved.prepayment, saved.partner_terms]).toEqual([
          "500.00",
          null,
          null,
        ]);
      } finally {
        const restored = await request.patch(`/api/contacts/${contactId}`, {
          data: { partner_id: contact.partner_id },
        });
        expect(restored.ok(), await restored.text()).toBe(true);
        // The case itself goes in afterEach.
        await request.delete(`/api/dictionaries/partners/${partner.id}`);
      }
    });

    test("a payment deleted a moment ago does not hold the case", async ({ page }) => {
      // «Удалено · Отменить» keeps the payment for a few seconds; «Удалить кейс» pressed
      // in that time sends its deletion first instead of naming it.
      const contactId = await demoCard(page, 6 + index);
      const kept = await openCase(page, contactId);
      const paid = await page.request.post(`/api/cases/${kept.id}/payments`, {
        data: { paid_on: "2026-06-01", amount: "100", currency: "BYN" },
      });
      expect(paid.ok(), await paid.text()).toBe(true);

      await page.goto(`/app/clients/${contactId}`, { waitUntil: "networkidle" });
      const panel = casePanel(page, kept.number);
      await panel
        .locator("section.finance")
        .getByRole("button", { name: t("vault.delete"), exact: true })
        .click();
      await panel.getByRole("button", { name: t("cases.delete"), exact: true }).click();
      const ask = page.getByRole("dialog");
      await expect(ask).toContainText(t("cases.number", { number: kept.number }));
      await expect(panel.getByRole("alert")).toHaveCount(0);
      await ask.getByRole("button", { name: t("cases.delete"), exact: true }).click();
      await expect(panel).toHaveCount(0);
      expect((await page.request.get(`/api/cases/${kept.id}`)).status()).toBe(404);
      made = null;
    });

    test("a case whose partner payout is marked paid is not deleted", async ({ page }) => {
      // Owner's answer 2026-11-20: the paid payout holds the case like a payment, and the
      // refusal names it instead of asking. Other demo cards than the test above uses.
      const contactId = await demoCard(page, 2 + index);
      const opened = await page.request.post(
        `/api/contacts/${contactId}/cases?allow_case_in_work=true`,
        {
          data: {
            destination_id: await countryId(page, "PL"),
            submission_country_id: await countryId(page, "BY"),
            applicants: [{ contact_id: contactId }],
          },
        },
      );
      expect(opened.ok(), await opened.text()).toBe(true);
      const [kept] = (await opened.json()) as { id: string; number: number }[];
      made = kept.id;
      const paid = await page.request.patch(`/api/cases/${kept.id}/finance`, {
        data: {
          price: "500",
          partner_terms: "fixed",
          partner_fixed: "50",
          partner_paid_on: "2026-06-01",
        },
      });
      expect(paid.ok(), await paid.text()).toBe(true);

      await page.goto(`/app/clients/${contactId}`, { waitUntil: "networkidle" });
      const panel = page.locator("article.case-panel").filter({
        has: page.getByRole("heading", { name: t("cases.number", { number: kept.number }) }),
      });
      await panel.getByRole("button", { name: t("cases.delete"), exact: true }).click();
      await expect(panel.getByRole("alert")).toContainText(t("cases.keptBy.partner_paid"));
      await expect(page.getByRole("dialog")).toHaveCount(0);
      expect((await page.request.get(`/api/cases/${kept.id}`)).status()).toBe(200);
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

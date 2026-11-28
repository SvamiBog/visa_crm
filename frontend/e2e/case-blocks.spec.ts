import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The cases of one client are kept apart on the card (pilot fix 9, docs/decisions.md →
// «Пилот: правки визовика (2026-11-19)», T4). The specialist put payments into №11 Польша
// instead of №9 Испания because she did not see Spain below. Now each case is a block of
// its own with «№9 · Испания · Туризм» and its stage in the head; with two cases or more a
// list of them stands at the top and leads to each; a case with a result is folded; the
// payment form and its confirmation name the case the payment goes to. Phone and desktop.
//
// The spec makes cards of its own with a unique name and keeps them: other specs borrow
// the demo's cards (search «demo_»), never these. Like Nikita, the client files with a
// family member, so neither case is a single case in work that the board's test could
// pick while this one runs.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Country = { id: string; code: string; name_ru: string; name_en: string };
type Case = { id: string; number: number; contact_id: string };
type Applicant = { contact_id: string } | { last_name: string; first_name: string; label: string };

async function countries(page: Page): Promise<Map<string, Country>> {
  const response = await page.request.get("/api/reference/countries");
  expect(response.ok(), await response.text()).toBe(true);
  return new Map(((await response.json()) as Country[]).map((c) => [c.code, c]));
}

/** Cases for the client and a family member, filed together: one per applicant. */
async function openCases(
  page: Page,
  contactId: string,
  family: Applicant,
  destination: Country,
  submission: Country,
  purposeId: string | null = null,
): Promise<Case[]> {
  const response = await page.request.post(
    `/api/contacts/${contactId}/cases?allow_case_in_work=true`,
    {
      data: {
        destination_id: destination.id,
        submission_country_id: submission.id,
        purpose_id: purposeId,
        applicants: [{ contact_id: contactId }, family],
      },
    },
  );
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as Case[];
}

/** The case's block on the card, found by the number in its head. */
function block(page: Page, title: string): Locator {
  return page
    .locator("article.case-panel")
    .filter({ has: page.getByRole("heading", { level: 3, name: title }) });
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

  test(`each case is a block of its own, the form names its case (${lang}, ${width}px)`, async ({
    page,
  }) => {
    expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
    await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
    await page.setViewportSize({ width, height: 900 });
    const login = await page.request.post("/api/auth/login", {
      data: { email: EMAIL, password: PASSWORD },
    });
    expect(login.ok(), await login.text()).toBe(true);

    // Nikita's card from the pilot: №9 Spain in work, then №11 Poland with a visa, which
    // stands above it (the newest case first). Each filed with a family member.
    const stamp = `${lang}${Date.now()}`;
    const made = await page.request.post("/api/contacts", {
      data: { last_name: "Блоков", first_name: stamp },
    });
    expect(made.ok(), await made.text()).toBe(true);
    const contactId = ((await made.json()) as { id: string }).id;
    const country = await countries(page);
    const [spain, poland, belarus] = [country.get("ES")!, country.get("PL")!, country.get("BY")!];
    const purposes = await page.request.get("/api/dictionaries/purposes");
    expect(purposes.ok(), await purposes.text()).toBe(true);
    const purpose = ((await purposes.json()) as { id: string; name: string }[])[0];
    expect(purpose, "the demo has a visa purpose").toBeTruthy();
    const relative = { last_name: "Блокова", first_name: stamp, label: "жена" };
    const spainCases = await openCases(page, contactId, relative, spain, belarus, purpose.id);
    const inWork = spainCases.find((c) => c.contact_id === contactId)!;
    const wife = spainCases.find((c) => c.contact_id !== contactId)!.contact_id;
    const polandCases = await openCases(page, contactId, { contact_id: wife }, poland, belarus);
    const done = polandCases.find((c) => c.contact_id === contactId)!;
    const result = await page.request.patch(`/api/cases/${done.id}`, {
      data: {
        outcome: "visa",
        decided_on: new Date().toISOString().slice(0, 10),
        result_to_group: true,
      },
    });
    expect(result.ok(), await result.text()).toBe(true);

    const name = (c: Country) => (lang === "en" ? c.name_en : c.name_ru);
    const spainCase = `${t("cases.number", { number: inWork.number })} · ${name(spain)}`;
    const polandCase = `${t("cases.number", { number: done.number })} · ${name(poland)}`;

    await page.goto(`/app/clients/${contactId}?tab=cases`);
    const spainBlock = block(page, `${spainCase} · ${purpose.name}`);
    const polandBlock = block(page, polandCase);

    // Two blocks, each with its case, its stage and a frame of its own, apart.
    await expect(page.locator("article.case-panel")).toHaveCount(2);
    await expect(spainBlock).toHaveCount(1);
    await expect(polandBlock).toHaveCount(1);
    await expect(spainBlock.getByText(t("cases.stage.waiting"), { exact: true }).first()).toBeVisible();
    for (const panel of [spainBlock, polandBlock]) {
      const edge = await panel.evaluate((el) => getComputedStyle(el).borderTopWidth);
      expect(edge, "a case block has a frame of its own").not.toBe("0px");
    }
    const top = (await polandBlock.boundingBox())!;
    const below = (await spainBlock.boundingBox())!;
    expect(below.y - (top.y + top.height), "the blocks do not touch").toBeGreaterThanOrEqual(12);

    // The list of the cases at the top, leading to each.
    const list = page.getByRole("navigation", { name: t("caseBlocks.list") });
    await expect(list).toBeVisible();
    await expect(list.getByRole("link")).toHaveCount(2);
    await expect(list.getByRole("link", { name: spainCase })).toBeVisible();
    await expect(list.getByRole("link", { name: polandCase })).toBeVisible();

    // The case with a result is folded: its head shows, its finances do not.
    const unfold = polandBlock.getByRole("button", { name: t("caseBlocks.expand"), exact: true });
    await expect(unfold).toHaveAttribute("aria-expanded", "false");
    const polandFinance = polandBlock.getByRole("heading", { name: t("finance.heading") });
    await expect(polandFinance).toBeHidden();
    await expect(spainBlock.getByRole("heading", { name: t("finance.heading") })).toBeVisible();

    // The list opens a folded case and brings it into view.
    await list.getByRole("link", { name: polandCase }).click();
    await expect(
      polandBlock.getByRole("button", { name: t("caseBlocks.collapse"), exact: true }),
    ).toHaveAttribute("aria-expanded", "true");
    await expect(polandFinance).toBeVisible();
    await expect(polandBlock.getByRole("heading", { level: 3 })).toBeInViewport();

    // «+ Платёж» of Spain: the form and its confirmation say where the payment goes.
    await spainBlock.getByRole("button", { name: t("finance.addPayment"), exact: true }).click();
    const form = page.getByRole("form", { name: t("caseBlocks.paymentTo", { case: spainCase }) });
    await expect(form).toBeVisible();
    await expect(spainBlock.locator("form.finance-form")).toHaveCount(1);
    await form.getByLabel(t("finance.amount"), { exact: true }).fill("10");
    await form.getByRole("button", { name: t("common.save"), exact: true }).click();
    await expect(form).toHaveCount(0);
    const finance = spainBlock.locator("section.finance");
    await expect(finance.getByRole("status")).toHaveText(
      t("caseBlocks.paymentSaved", { case: spainCase }),
    );
    // …and its deletion names the case it leaves; the «saved» line goes.
    await finance.getByRole("button", { name: t("vault.delete"), exact: true }).click();
    await expect(
      page.getByRole("status").filter({ hasText: t("caseBlocks.paymentDeleted", { case: spainCase }) }),
    ).toBeVisible();
    await expect(finance.getByRole("status")).toHaveCount(0);

    // Nothing runs off the side of a phone.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, "no sideways scroll").toBeLessThanOrEqual(0);
  });
}

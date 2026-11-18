import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// Sortable tables (docs/decisions.md → «Сортировка таблиц»): a column heading is a button,
// the sorted column carries aria-sort, the order is in the URL, so «Назад» from a client's
// card keeps it. The client index is sorted by the server; on a phone its cards have no
// headings and the order is chosen from a list. Both languages, phone and desktop widths.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

const WIDTHS = [390, 1280] as const;
const PHONE = 720;
// The demo's own clients: their Telegram is demo_<n>_<m>. Other tests make and delete
// cards of their own meanwhile, so this one makes none and searches only the demo's.
// (The alphabet with «Ё» is proven in backend/tests/test_client_index.py.)
const DEMO = "demo_";

/** The names the server gives for the demo's clients in this order. */
async function serverNames(page: Page, sort: string): Promise<string[]> {
  const found = await page.request.post("/api/contacts/search", { data: { query: DEMO, sort } });
  expect(found.ok(), await found.text()).toBe(true);
  return ((await found.json()).items as { full_name: string }[]).map((row) => row.full_name);
}

function header(page: Page, name: string): Locator {
  return page.getByRole("columnheader", { name, exact: true });
}

/** Clicks a column's button and waits until the column shows the order. */
async function sortBy(page: Page, name: string, sort: "ascending" | "descending" | null) {
  await header(page, name).getByRole("button").click();
  if (sort) await expect(header(page, name)).toHaveAttribute("aria-sort", sort);
  else await expect(header(page, name)).not.toHaveAttribute("aria-sort");
}

function sortParam(page: Page, param = "sort"): string | null {
  return new URL(page.url()).searchParams.get(param);
}

/** The texts of a column of a table, by the column's position. */
async function column(table: Locator, index: number): Promise<string[]> {
  return table.locator(`tbody tr > td:nth-child(${index})`).allInnerTexts();
}

for (const lang of ["ru", "en"] as const) {
  for (const width of WIDTHS) {
    test.describe(`sorting in ${lang} at ${width}px`, () => {
      const t = (key: string) => {
        if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
        return LOCALES[lang][key];
      };
      const collator = new Intl.Collator(lang, { numeric: true });

      test.beforeEach(async ({ page }) => {
        expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
        await page.setViewportSize({ width, height: 900 });
        await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
        const login = await page.request.post("/api/auth/login", {
          data: { email: EMAIL, password: PASSWORD },
        });
        expect(login.ok(), await login.text()).toBe(true);
      });

      test("the client index sorts by name on the server and keeps it after a card", async ({
        page,
      }) => {
        const newest = await serverNames(page, "newest");
        const byName = await serverNames(page, "name");
        expect(newest.length).toBeGreaterThan(2);
        // The demo's names are Cyrillic: the alphabet of either language puts them alike.
        expect(byName).toEqual([...newest].sort(collator.compare));
        expect(byName).not.toEqual(newest);

        await page.goto("/app/clients");
        await page.getByRole("searchbox").fill(DEMO);
        const names = page.locator(".clients-table tbody .cell-client a");
        const shown = () => names.allInnerTexts();
        // By default the newest first, and no column is marked.
        await expect.poll(shown).toEqual(newest);
        await expect(page.locator(".clients-table th[aria-sort]")).toHaveCount(0);

        const choice = page.getByLabel(t("index.sort.label"));
        if (width <= PHONE) {
          // The cards have no headings on a phone: the order is chosen from the list.
          await expect(choice).toBeVisible();
          await choice.selectOption({ label: t("index.sort.nameAsc") });
          await expect.poll(shown).toEqual(byName);
          await choice.selectOption({ label: t("index.sort.nameDesc") });
          await expect.poll(shown).toEqual(await serverNames(page, "-name"));
          expect(sortParam(page)).toBe("-name");
          await choice.selectOption({ label: t("index.sort.nameAsc") });
        } else {
          await expect(choice).toBeHidden();
          const client = t("index.colClient");
          await sortBy(page, client, "ascending");
          await expect.poll(shown).toEqual(byName);
          await sortBy(page, client, "descending");
          await expect.poll(shown).toEqual(await serverNames(page, "-name"));
          expect(sortParam(page)).toBe("-name");
          // The third click brings the table's own order back.
          await sortBy(page, client, null);
          expect(sortParam(page)).toBeNull();
          await expect.poll(shown).toEqual(newest);
          // The date of «Дальше» is a column too.
          await sortBy(page, t("index.colNext"), "ascending");
          await expect.poll(shown).toEqual(await serverNames(page, "next"));
          await sortBy(page, client, "ascending");
        }
        expect(sortParam(page)).toBe("name");
        await expect.poll(shown).toEqual(byName);

        // A card and back: the same order.
        await names.first().click();
        await expect(page).toHaveURL(/\/app\/clients\/[0-9a-f-]+$/);
        await page.goBack();
        expect(sortParam(page)).toBe("name");
        await expect.poll(shown).toEqual(byName);
        if (width <= PHONE) await expect(choice).toHaveValue("name");
        else await expect(header(page, t("index.colClient"))).toHaveAttribute("aria-sort", "ascending");
      });

      test("a waiting list sorts in the browser by days, names and dates", async ({ page }) => {
        await page.goto("/app/queues");
        const table = page
          .locator("section.card")
          .filter({ has: page.getByRole("heading", { name: t("queues.waitingList") }) })
          .locator("table");
        await expect(table.locator("tbody tr").first()).toBeVisible();
        // Who has waited longest first: the date of queueing is the table's own order.
        await expect(header(page, t("queues.queuedOn"))).toHaveAttribute("aria-sort", "ascending");

        // Days compare as numbers, not as text («9» after «12»).
        await sortBy(page, t("queues.waits"), "descending");
        expect(sortParam(page)).toBe("-waits");
        const days = (await column(table, 8)).map((text) => Number(text.match(/\d+/)?.[0]));
        expect(days).toEqual([...days].sort((a, b) => b - a));

        await sortBy(page, t("queues.applicant"), "ascending");
        const applicants = (await column(table, 1)).map((cell) => cell.split("\n")[0]);
        expect(applicants).toEqual([...applicants].sort(collator.compare));
        await expect(header(page, t("queues.queuedOn"))).not.toHaveAttribute("aria-sort");

        // The order is in the URL: a reload (or a link) opens the table sorted the same way.
        await page.reload();
        await expect(header(page, t("queues.applicant"))).toHaveAttribute("aria-sort", "ascending");
        expect(sortParam(page)).toBe("applicant");
      });

      test("bookings, the log and the reports sort by their headings", async ({ page }) => {
        await page.goto("/app/bookings");
        const deadline = t("bookings.colDeadline");
        await expect(header(page, deadline)).toHaveAttribute("aria-sort", "ascending");
        await sortBy(page, deadline, "descending");
        expect(sortParam(page)).toBe("-deadline");
        await sortBy(page, deadline, "ascending");
        // The table's own order needs no parameter.
        expect(sortParam(page)).toBeNull();
        await sortBy(page, t("bookings.colClient"), "ascending");
        expect(sortParam(page)).toBe("client");

        // The log: the time heading and the order list are one and the same order.
        await page.goto("/app/audit");
        const time = t("audit.colTime");
        await expect(header(page, time)).toHaveAttribute("aria-sort", "descending");
        await sortBy(page, time, "ascending");
        expect(sortParam(page)).toBe("time");
        await expect(page.getByLabel(t("audit.order"))).toHaveValue("asc");
        const moments = await page.locator("tbody time").evaluateAll((nodes) =>
          nodes.map((node) => Date.parse(node.getAttribute("datetime") ?? "")),
        );
        // The server pages the log by event id: events of overlapping requests may differ
        // by milliseconds the other way, so only the ends are compared.
        expect(moments.length).toBeGreaterThan(1);
        expect(moments[0]).toBeLessThanOrEqual(moments[moments.length - 1]);
        await page.getByLabel(t("audit.order")).selectOption("desc");
        await expect(header(page, time)).toHaveAttribute("aria-sort", "descending");
        expect(sortParam(page)).toBeNull();

        // A report: the channels start from the most consultations; profit from the largest.
        await page.goto("/app/analytics");
        await page.getByRole("tab", { name: t("analytics.preset.year") }).click();
        const channels = page
          .locator("section.card")
          .filter({ has: page.getByRole("heading", { name: t("analytics.channels") }) });
        const consultations = channels.getByRole("columnheader", {
          name: t("analytics.col.consultations"),
          exact: true,
        });
        await expect(consultations.first()).toHaveAttribute("aria-sort", "descending");
        await channels
          .getByRole("columnheader", { name: t("analytics.col.visas"), exact: true })
          .first()
          .getByRole("button")
          .click();
        expect(sortParam(page, "sort_channels")).toBe("-visas");
        const visas = (await column(channels.locator("table").first(), 4)).map(Number);
        expect(visas).toEqual([...visas].sort((a, b) => b - a));
      });
    });
  }
}

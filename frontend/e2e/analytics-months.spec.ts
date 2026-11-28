import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

// Profit by the month of payment (backlog 1.18): a month is the money that came in less
// the money that went out, so it may be negative: the talon was paid in May, the client
// paid in June. Such a month shows its number with a minus and an empty bar; the hint of
// «По месяцам» and the note of the «Прибыль» figure say the profit goes by payments.
// The demo has no negative month and other tests read its money meanwhile, so the report
// is the server's own with the profit of its last two months replaced. The sums themselves
// are proven in backend/tests/test_analytics.py. Both languages, phone and desktop widths.

type Lang = "ru" | "en";
type Report = { months: { month: string; profit: string | null }[]; money: unknown };

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

const WIDTHS = [390, 1280] as const;
const EARNED = "650.50";
const LOST = "-150.00";

for (const lang of ["ru", "en"] as const) {
  for (const width of WIDTHS) {
    test(`a month in the red in ${lang} at ${width}px`, async ({ page }) => {
      const t = (key: string) => {
        if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
        return LOCALES[lang][key];
      };
      // As the page writes money: kopecks only when there are some.
      const shown = (amount: string) =>
        new Intl.NumberFormat(lang, {
          minimumFractionDigits: Number.isInteger(Number(amount)) ? 0 : 2,
          maximumFractionDigits: 2,
        }).format(Number(amount));

      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);

      // The last month of the period lost money, the one before it earned some.
      await page.route("**/api/analytics?*", async (route) => {
        const response = await route.fetch();
        const report = (await response.json()) as Report;
        expect(report.money, "the demo specialist has «Финансы кейсов»").not.toBeNull();
        expect(report.months.length).toBeGreaterThan(1);
        report.months.at(-2)!.profit = EARNED;
        report.months.at(-1)!.profit = LOST;
        await route.fulfill({ response, json: report });
      });
      await page.goto("/app/analytics");

      await expect(page.getByText(t("analytics.kpi.profitNote"), { exact: true })).toBeVisible();
      const months = page
        .locator("section.card")
        .filter({ has: page.getByRole("heading", { name: t("analytics.months"), exact: true }) });
      await expect(months.getByText(t("analytics.monthsHint"), { exact: true })).toBeVisible();

      // The number with its minus and nothing drawn in the bar; the month before has a bar.
      const rows = months.locator("tbody tr");
      const lost = rows.last().locator("td").last();
      await expect(lost).toHaveText(shown(LOST));
      expect(await lost.innerText()).toMatch(/^-/);
      await expect(lost.locator(".bar-fill")).toHaveCSS("width", "0px");
      const earned = rows.nth((await rows.count()) - 2).locator("td").last();
      await expect(earned).toHaveText(shown(EARNED));
      const drawn = await earned.locator(".bar-fill").evaluate((bar) => bar.clientWidth);
      expect(drawn, "the bar of a month that earned").toBeGreaterThan(0);

      // The number is whole on the screen: the table scrolls inside its card on a phone
      // (the card's edge may cut a fraction of a pixel of the cell's side), the page itself
      // never sideways.
      await lost.scrollIntoViewIfNeeded();
      await expect(lost).toBeInViewport({ ratio: 0.99 });
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, "the page does not scroll sideways").toBeLessThanOrEqual(0);
    });
  }
}

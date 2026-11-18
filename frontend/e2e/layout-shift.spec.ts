import { expect, test } from "@playwright/test";

// Pages that jumped while their data came (UI audit of 2026-11-17, CLS by the Layout
// Instability API): the form under «Справочники» was pushed down by the list, the country
// filter of «Брони» appeared above the table, the filters of «Чек-листы» joined the country
// once it was chosen, «Список / Доска» of «Клиенты» took a row above the list on a phone.
// Each page now waits for what changes the layout above before it draws what is below. The
// sum of the shifts of a fresh load stays under GOOD_CLS on a phone and on a desktop, in both
// languages (the English texts are fetched on demand).
//
// A page jumps only when that data comes after the first paint. On a fast stand it often comes
// before, so each page holds back the answer that changes its layout (`slow`) by HOLD_MS, as
// a slow connection would.
//
// Google's fonts are left out: a font that comes late redraws the text in its own width, and
// that is a matter of how the fonts load, not of the order the page draws its data in.

const GOOD_CLS = 0.05;
const HOLD_MS = 1000;
const WIDTHS = [375, 1280];
const PAGES = [
  // The modules: whether «Список / Доска» stands next to the search.
  { path: "/app/clients", slow: /\/api\/workspace$/ },
  // The list above the form of a new purpose.
  { path: "/app/dictionaries", slow: /\/api\/dictionaries\/purposes$/ },
  // The rows: the filter by country stands above them when they come from several countries.
  { path: "/app/bookings", slow: /\/api\/bookings\/deadlines$/ },
  // The countries with a list (the first is chosen and its filters join it), then the list.
  { path: "/app/checklists", slow: /\/api\/checklists(\/[^/?]+)?(\?.*)?$/ },
];

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

declare global {
  interface Window {
    __layoutShift: number;
  }
}

test.beforeEach(async ({ context, page }) => {
  await context.route(/fonts\.(googleapis|gstatic)\.com/, (route) => route.abort());
  // CI runs the stand as staging, whose strip (EnvBanner in src/App.tsx) comes with
  // /api/health and moves every page down once. The real system has no strip, so the test
  // answers as the real system.
  await context.route(/\/api\/health$/, (route) =>
    route.fulfill({ json: { status: "ok", env: "production" } }),
  );
  // In the context, not the page: one observer per document, counted from its first paint.
  await context.addInitScript(() => {
    window.__layoutShift = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as (PerformanceEntry & {
        value: number;
        hadRecentInput: boolean;
      })[]) {
        if (!entry.hadRecentInput) window.__layoutShift += entry.value;
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
  const login = await page.request.post("/api/auth/login", {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
});

for (const lang of ["ru", "en"] as const) {
  for (const width of WIDTHS) {
    for (const { path, slow } of PAGES) {
      test(`${path} does not jump while it loads, ${lang}, ${width}px wide`, async ({
        context,
        page,
      }) => {
        await context.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
        await context.route(slow, async (route) => {
          await new Promise((resolve) => setTimeout(resolve, HOLD_MS));
          await route.continue();
        });
        await page.setViewportSize({ width, height: 900 });
        await page.goto(path, { waitUntil: "networkidle" });
        await expect(page.locator(".page .card").first()).toBeVisible();
        const shift = await page.evaluate(() => window.__layoutShift);
        expect(shift, `layout shift of ${path}`).toBeLessThan(GOOD_CLS);
      });
    }
  }
}

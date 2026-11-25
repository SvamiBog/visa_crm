import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// One kind of control in one size, weights on the scale (backlog 2.15, the ui-audit of
// 25.11.2026):
// - in a row of «Цели визы» the kind's select and the rename field are as tall as the
//   row's buttons: compact on a desktop, a finger's 44px on a touch screen;
// - the month switch of the funnel keeps the 8px gap of every row of buttons;
// - bold text weighs 600, not the browser's 700 (the funnel's numbers);
// - the portal's link field is a form field, as tall as the fields of «+ Платёж»;
// - on a touch screen the links «Все клиенты», «Все очереди», the queues of the home
//   page and «← Все клиенты» are finger-sized targets;
// - a card's heading is 16px in the themes warm and minimal (it was 15 and 13).
// Both languages, a phone's touch screen and a desktop's mouse, all four themes.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

const THEMES = ["blue", "dark", "warm", "minimal"] as const;
// The gap of a row of buttons (.actions, .form-actions, .card-head-actions in styles.css).
const MIN_GAP = 8;
// A finger's target (decisions «Отклик на нажатие»).
const FINGER = 44;
const MAX_WEIGHT = 600;
const CARD_HEADING_PX = 16;

const DEVICES = [
  { name: "phone", viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true },
  { name: "desktop", viewport: { width: 1280, height: 900 }, hasTouch: false, isMobile: false },
] as const;

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

async function logIn(page: Page): Promise<void> {
  expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
  const login = await page.request.post("/api/auth/login", {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
}

/** A demo card whose case shows «Страница для клиента»: the portal is open and the
    case has a checklist. The demo's own cards, not ones another test makes. */
async function demoCardWithPortal(page: Page): Promise<string> {
  const found = await page.request.post("/api/contacts/search", { data: { query: "demo_" } });
  expect(found.ok()).toBe(true);
  for (const { id } of ((await found.json()).items as { id: string }[]).reverse()) {
    const card = (await (await page.request.get(`/api/contacts/${id}`)).json()) as {
      cases: { id: string }[] | null;
    };
    for (const c of card.cases ?? []) {
      const portal = await page.request.get(`/api/cases/${c.id}/portal`);
      if (!portal.ok() || !(await portal.json()).token) continue;
      const pack = (await (await page.request.get(`/api/cases/${c.id}/package`)).json()) as {
        items: unknown[];
      };
      if (pack.items.length > 0) return id;
    }
  }
  throw new Error("The demo has no case with an open portal and a checklist");
}

const height = (el: Locator) => el.evaluate((e) => Math.round(e.getBoundingClientRect().height));

/** Switches the theme and waits for its fonts: a theme's own font loads on first use,
    and a box measured before and after the swap differs by a few pixels. */
async function setTheme(page: Page, theme: string): Promise<void> {
  await page.evaluate(async (th) => {
    document.documentElement.dataset.uiTheme = th;
    await document.fonts.ready;
  }, theme);
}

/** Visible links of a selector whose box is shorter than `min`. */
function smallTargets(page: Page, selector: string, min: number): Promise<string[]> {
  return page.evaluate(
    ([sel, least]) =>
      [...document.querySelectorAll<HTMLElement>(sel)]
        .filter((el) => el.offsetParent !== null)
        .map((el) => ({ el, r: el.getBoundingClientRect() }))
        .filter(({ r }) => r.height < least - 0.5)
        .map(({ el, r }) => `«${el.textContent?.trim()}»: ${r.height.toFixed(1)}px`),
    [selector, min] as const,
  );
}

for (const lang of ["ru", "en"] as const) {
  const t = (key: string) => {
    if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
    return LOCALES[lang][key];
  };

  for (const device of DEVICES) {
    test.describe(`sizes on a ${device.name} (${lang})`, () => {
      test.use({
        viewport: device.viewport,
        hasTouch: device.hasTouch,
        isMobile: device.isMobile,
      });

      test.beforeEach(async ({ page }) => {
        await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
        await logIn(page);
      });

      test("a row of «Цели визы»: the select and the rename field match the row's buttons", async ({
        page,
      }) => {
        await page.goto("/app/dictionaries");
        const row = page.locator(".plain-list > li").filter({ has: page.locator(".actions select") }).first();
        await expect(row).toBeVisible();
        const select = row.locator(".actions select");
        const rename = row.getByRole("button", { name: t("dict.rename"), exact: true });
        for (const theme of THEMES) {
          await setTheme(page, theme);
          expect
            .soft(await height(select), `the kind's select is as tall as «${t("dict.rename")}» (${theme})`)
            .toBe(await height(rename));
        }

        await rename.click();
        const form = page.locator(".plain-list .inline-form");
        const field = form.getByRole("textbox");
        const save = form.getByRole("button", { name: t("common.save"), exact: true });
        const cancel = form.getByRole("button", { name: t("common.cancel"), exact: true });
        await expect(field).toBeVisible();
        for (const theme of THEMES) {
          await setTheme(page, theme);
          const buttons = await height(save);
          expect.soft(await height(cancel), `the rename's buttons alike (${theme})`).toBe(buttons);
          expect
            .soft(await height(field), `the rename field is as tall as its buttons (${theme})`)
            .toBe(buttons);
          if (device.hasTouch) {
            expect.soft(buttons, `the rename's row is finger-sized (${theme})`).toBeGreaterThanOrEqual(FINGER);
          }
        }
      });

      test("the home page: month switch apart, bold on the scale, links finger-sized", async ({
        page,
      }) => {
        await page.goto("/app");
        const prev = page.getByRole("button", { name: t("home.prevMonth"), exact: true });
        await expect(prev).toBeVisible();
        const funnelNumber = page.locator(".funnel strong").first();
        await expect(funnelNumber).toBeVisible();
        for (const theme of THEMES) {
          await setTheme(page, theme);
          // Both edges in one go, so a late font cannot move one between the two readings.
          const gap = await page.locator(".month-switch").evaluate((row) => {
            const [a, b] = row.querySelectorAll(".btn");
            return b.getBoundingClientRect().left - a.getBoundingClientRect().right;
          });
          expect.soft(gap, `«‹» and «›» keep a gap (${theme})`).toBeGreaterThanOrEqual(MIN_GAP - 0.5);
          const weight = await funnelNumber.evaluate((e) => Number(getComputedStyle(e).fontWeight));
          expect.soft(weight, `a funnel's number is bold at 600 at most (${theme})`).toBeLessThanOrEqual(MAX_WEIGHT);
          const heading = page.locator(".card-head .h2").first();
          const size = await heading.evaluate((e) => parseFloat(getComputedStyle(e).fontSize));
          if (theme === "warm" || theme === "minimal") {
            expect.soft(size, `a card's heading is ${CARD_HEADING_PX}px (${theme})`).toBe(CARD_HEADING_PX);
          }
          if (device.hasTouch) {
            expect
              .soft(
                await smallTargets(page, ".card-head a.small, .home-list a", FINGER),
                `the home page's links are finger-sized (${theme})`,
              )
              .toEqual([]);
          }
        }
      });

      test("a client's card: the portal's link is a form field, «← Все клиенты» finger-sized", async ({
        page,
      }) => {
        const client = await demoCardWithPortal(page);
        await page.goto(`/app/clients/${client}`);
        const link = page.getByRole("textbox", { name: t("portal.link"), exact: true });
        await expect(link).toBeVisible();
        await page.getByRole("button", { name: t("finance.addPayment"), exact: true }).first().click();
        const amount = page.locator("form.finance-form .field input").first();
        await expect(amount).toBeVisible();
        const back = page.getByRole("link", { name: `← ${t("contacts.backToList")}`, exact: true });
        for (const theme of THEMES) {
          await setTheme(page, theme);
          expect
            .soft(await height(link), `the link field is as tall as a payment's field (${theme})`)
            .toBe(await height(amount));
          if (device.hasTouch) {
            expect
              .soft(await height(back), `«← ${t("contacts.backToList")}» is finger-sized (${theme})`)
              .toBeGreaterThanOrEqual(FINGER);
            const font = await link.evaluate((e) => parseFloat(getComputedStyle(e).fontSize));
            expect.soft(font, `the link field does not zoom the phone (${theme})`).toBeGreaterThanOrEqual(16);
          }
        }
      });
    });
  }
}

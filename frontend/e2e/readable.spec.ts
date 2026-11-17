import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// What the Impeccable audit of 2026-11-17 found hard to read or to hit on a phone, held in
// every theme and both languages:
// - text against what is under it at least 4.5:1 (WCAG 1.4.3): the labels of the phone's
//   bottom bar, grey text under a page's title, «Написать в Telegram»;
// - the edge of a text field at least 3:1 against the card (WCAG 1.4.11), so an empty
//   field is seen without its label;
// - on a touch screen a small button is still a finger's 44px tall;
// - the browser's tab names the section, never the client (WCAG 2.4.2).
// The theme is switched in place, so all four are checked on one load.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

const THEMES = ["blue", "dark", "warm", "minimal"] as const;
const TEXT_CONTRAST = 4.5;
const EDGE_CONTRAST = 3;
const FINGER = 44;

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Found = { id: string; telegram: string | null };
type Measured = { label: string; ratio: number };

// A phone: narrow, and a finger rather than a mouse (pointer: coarse).
test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

/** The oldest demo card whose Telegram is a username, so it shows «Написать в Telegram». */
async function chatCard(page: Page): Promise<string> {
  const found = await page.request.post("/api/contacts/search", { data: {} });
  expect(found.ok()).toBe(true);
  for (const { id, telegram } of ((await found.json()).items as Found[]).reverse()) {
    // The rule of telegramUrl in src/contacts.ts.
    if (telegram && /^[A-Za-z0-9_]{5,32}$/.test(telegram)) return id;
  }
  throw new Error("The demo has no card with a Telegram username");
}

/** The contrast of each shown element's text (or, with `edge`, its border) against the
    nearest opaque background behind it. */
function contrasts(page: Page, selector: string, edge = false): Promise<Measured[]> {
  return page.evaluate(
    ([sel, useEdge]) => {
      type Rgb = { r: number; g: number; b: number; a: number };
      const parse = (color: string): Rgb | null => {
        const m = color.match(/rgba?\(([^)]+)\)/);
        if (!m) return null;
        const [r, g, b, a = 1] = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
        return { r, g, b, a };
      };
      const luminance = ({ r, g, b }: Rgb) => {
        const lin = (c: number) => {
          const v = c / 255;
          return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
        };
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
      };
      const behind = (start: Element | null): Rgb => {
        for (let p = start; p; p = p.parentElement) {
          const color = parse(getComputedStyle(p).backgroundColor);
          if (color && color.a === 1) return color;
        }
        return parse(getComputedStyle(document.body).backgroundColor) ?? { r: 255, g: 255, b: 255, a: 1 };
      };
      return [...document.querySelectorAll<HTMLElement>(sel)]
        .filter((el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden")
        .map((el) => {
          const style = getComputedStyle(el);
          const front = parse(useEdge ? style.borderTopColor : style.color);
          const back = behind(useEdge ? el.parentElement : el);
          const [hi, lo] = [luminance(front ?? back), luminance(back)].sort((x, y) => y - x);
          const label = (el.textContent?.trim() || el.getAttribute("name") || el.tagName).slice(0, 40);
          return { label, ratio: Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100 };
        });
    },
    [selector, edge] as const,
  );
}

const below = (items: Measured[], min: number) =>
  items.filter((item) => item.ratio < min).map((item) => `«${item.label}» ${item.ratio}:1`);

for (const lang of ["ru", "en"] as const) {
  test(`readable and easy to hit on a phone in every theme (${lang})`, async ({ page }) => {
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };
    expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
    await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
    const login = await page.request.post("/api/auth/login", {
      data: { email: EMAIL, password: PASSWORD },
    });
    expect(login.ok(), await login.text()).toBe(true);
    const app = t("app.title");

    // A client's card: the bottom bar, the grey line under the name, Telegram, small buttons.
    await page.goto(`/app/clients/${await chatCard(page)}`);
    const writeTelegram = page.getByRole("link", { name: t("contacts.writeTelegram"), exact: true });
    await expect(writeTelegram).toBeVisible();
    await expect(page.getByRole("heading", { name: t("contacts.timeline") })).toBeVisible();
    const name = (await page.getByRole("heading", { level: 1 }).textContent())?.trim() ?? "";
    expect(name).not.toBe("");
    // Soft here and below: one run lists every broken rule.
    await expect
      .soft(page, "the tab names the section, not the client")
      .toHaveTitle(`${t("nav.clients")} · ${app}`, { timeout: 2000 });

    const small = await page
      .locator(".page .btn-sm")
      .evaluateAll((buttons) =>
        buttons
          .filter((b) => b.getClientRects().length > 0)
          .map((b) => ({ label: b.textContent?.trim() ?? "", height: b.getBoundingClientRect().height })),
      );
    expect(small.length, "the card has small buttons to measure").toBeGreaterThan(0);
    expect
      .soft(
        small.filter((b) => b.height < FINGER - 0.5).map((b) => `«${b.label}» ${b.height}px`),
        "a small button is a finger tall on a touch screen",
      )
      .toEqual([]);

    for (const theme of THEMES) {
      await page.evaluate((th) => (document.documentElement.dataset.uiTheme = th), theme);
      const where = `${theme}, ${lang}`;
      expect
        .soft(below(await contrasts(page, ".tabbar .bar-a span"), TEXT_CONTRAST), `bottom bar labels (${where})`)
        .toEqual([]);
      expect
        .soft(below(await contrasts(page, ".page-header .muted"), TEXT_CONTRAST), `grey text under the title (${where})`)
        .toEqual([]);
      expect
        .soft(below(await contrasts(page, ".btn-telegram"), TEXT_CONTRAST), `«Написать в Telegram» (${where})`)
        .toEqual([]);
    }

    // A new client's form: the edges of its fields.
    await page.goto("/app/clients?new=1");
    await expect(page.locator(".field input").first()).toBeVisible();
    for (const theme of THEMES) {
      await page.evaluate((th) => (document.documentElement.dataset.uiTheme = th), theme);
      expect
        .soft(
          below(await contrasts(page, ".field input, .field select, .field textarea", true), EDGE_CONTRAST),
          `edges of text fields (${theme}, ${lang})`,
        )
        .toEqual([]);
    }

    // Every section names its tab.
    for (const [path, key] of [
      ["/app", "nav.home"],
      ["/app/vault", "nav.vault"],
      ["/settings", "nav.account"],
    ] as const) {
      await page.goto(path);
      await expect.soft(page, `the tab of ${path}`).toHaveTitle(`${t(key)} · ${app}`, { timeout: 2000 });
    }
  });
}

import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The app's screens read at a glance (audit with taste-skill's rules for app screens):
// - a column of figures and its heading stand on one edge (the headings stayed on the
//   left above right-aligned numbers in «Аналитика» and «Ближайшие подачи»);
// - the keyboard's focus is a ring in the theme's accent, not the browser's black frame;
// - small grey text and the Telegram button's label keep 4.5:1 in every theme (the menu's
//   group names were 3.2:1 in the warm and minimal themes).
// Both languages; the theme is switched in place, so all four are checked on one load.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

const THEMES = ["blue", "dark", "warm", "minimal"] as const;
// WCAG AA for text under 18px.
const MIN_CONTRAST = 4.5;

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

async function signIn(page: Page, lang: Lang, width: number): Promise<void> {
  expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
  await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
  await page.setViewportSize({ width, height: 900 });
  const login = await page.request.post("/api/auth/login", {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(login.ok(), await login.text()).toBe(true);
}

function setTheme(page: Page, theme: string): Promise<void> {
  return page.evaluate((th) => {
    document.documentElement.dataset.uiTheme = th;
  }, theme);
}

/** Headings of figure columns whose text does not end where the figures under them do. */
function headingsOffTheirFigures(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const textRight = (cell: Element): number | null => {
      const range = document.createRange();
      range.selectNodeContents(cell);
      const box = range.getBoundingClientRect();
      return box.width > 0 ? box.right : null;
    };
    const off: string[] = [];
    for (const table of document.querySelectorAll("table.tbl")) {
      const heads = [...table.querySelectorAll("thead th")];
      const row = table.querySelector("tbody tr");
      if (!row) continue;
      heads.forEach((th, i) => {
        if (!th.classList.contains("money-cell")) return;
        const cell = row.children[i];
        // A cell with a bar ends with its figure too.
        const head = textRight(th);
        const figure = cell && textRight(cell);
        if (head !== null && figure && Math.abs(head - figure) > 2) {
          off.push(`«${th.textContent?.trim()}»: ${Math.round(figure - head)}px`);
        }
      });
    }
    return off;
  });
}

/** Text of the given elements that is below `min` against what is behind it. */
function lowContrast(page: Page, selector: string, min: number): Promise<string[]> {
  return page.evaluate(
    ([sel, limit]) => {
      const rgba = (c: string) => {
        const p = c.match(/[\d.]+/g)?.map(Number) ?? [0, 0, 0];
        return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
      };
      const over = (top: ReturnType<typeof rgba>, under: ReturnType<typeof rgba>) => ({
        r: top.r * top.a + under.r * (1 - top.a),
        g: top.g * top.a + under.g * (1 - top.a),
        b: top.b * top.a + under.b * (1 - top.a),
        a: 1,
      });
      const lum = ({ r, g, b }: ReturnType<typeof rgba>) => {
        const f = (v: number) => {
          const s = v / 255;
          return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
        };
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
      };
      const ground = (el: Element) => {
        const chain: Element[] = [];
        for (let e: Element | null = el; e; e = e.parentElement) chain.unshift(e);
        return chain.reduce(
          (col, e) => over(rgba(getComputedStyle(e).backgroundColor), col),
          { r: 255, g: 255, b: 255, a: 1 },
        );
      };
      const low: string[] = [];
      for (const el of document.querySelectorAll<HTMLElement>(sel)) {
        if (!el.offsetParent || !el.textContent?.trim()) continue;
        const back = ground(el);
        const [x, y] = [lum(over(rgba(getComputedStyle(el).color), back)), lum(back)].sort(
          (a, b) => b - a,
        );
        const ratio = (x + 0.05) / (y + 0.05);
        if (ratio < limit) low.push(`«${el.textContent.trim().slice(0, 30)}»: ${ratio.toFixed(2)}`);
      }
      return low;
    },
    [selector, min] as const,
  );
}

/** A colour variable of styles.css (`--c-primary`), as the browser computes it. */
function themeColor(page: Page, variable: string): Promise<string> {
  return page.evaluate((name) => {
    const probe = document.createElement("span");
    probe.style.color = `var(${name})`;
    document.body.append(probe);
    const color = getComputedStyle(probe).color;
    probe.remove();
    return color;
  }, variable);
}

for (const lang of ["ru", "en"] as const) {
  const t = (key: string) => {
    if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
    return LOCALES[lang][key];
  };

  test(`a column of figures and its heading stand on one edge (${lang})`, async ({ page }) => {
    await signIn(page, lang, 1280);
    for (const [path, ready] of [
      ["/app/analytics", t("analytics.money")],
      ["/app", t("home.upcoming")],
    ] as const) {
      await page.goto(path);
      await expect(page.getByRole("heading", { name: ready, exact: true })).toBeVisible();
      await page.waitForLoadState("networkidle");
      expect.soft(await headingsOffTheirFigures(page), `headings on ${path}`).toEqual([]);
    }
  });

  test(`the keyboard's focus is a ring in the theme's accent (${lang})`, async ({ page }) => {
    await signIn(page, lang, 1280);
    await page.goto("/app/clients");
    const add = page.getByRole("button", { name: t("contacts.add"), exact: true });
    await expect(add).toBeVisible();
    for (let i = 0; i < 40 && !(await add.evaluate((b) => b === document.activeElement)); i++) {
      await page.keyboard.press("Tab");
    }
    await expect(add).toBeFocused();
    for (const theme of THEMES) {
      await setTheme(page, theme);
      await expect.soft(add, `ring style (${theme})`).toHaveCSS("outline-style", "solid");
      await expect.soft(add, `ring width (${theme})`).toHaveCSS("outline-width", "2px");
      await expect
        .soft(add, `ring colour (${theme})`)
        .toHaveCSS("outline-color", await themeColor(page, "--c-primary"), { timeout: 1000 });
    }
  });

  test(`small grey text keeps ${MIN_CONTRAST}:1 in every theme (${lang})`, async ({ page }) => {
    await signIn(page, lang, 1280);
    // A card with a Telegram username shows «Написать в Telegram».
    const found = await page.request.post("/api/contacts/search", { data: {} });
    const chat = ((await found.json()).items as { id: string; telegram: string | null }[]).find(
      (c) => c.telegram && /^[A-Za-z0-9_]{5,32}$/.test(c.telegram),
    );
    expect(chat, "the demo has a card with a Telegram username").toBeTruthy();
    for (const [path, selector] of [
      ["/app", ".nav-heading, .sidebar-area, .account-email, .muted, .kpi-label, .kpi-note"],
      ["/app/clients", ".tab, .tab-count, .muted"],
      [`/app/clients/${chat?.id}`, ".btn-telegram, .muted, .page-header a"],
    ] as const) {
      await page.goto(path);
      await expect(page.locator("h1").first()).toBeVisible();
      await page.waitForLoadState("networkidle");
      for (const theme of THEMES) {
        await setTheme(page, theme);
        expect
          .soft(await lowContrast(page, selector, MIN_CONTRAST), `${path}, ${theme}`)
          .toEqual([]);
      }
    }
  });
}

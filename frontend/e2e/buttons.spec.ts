import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// Buttons on a client's card read as buttons, on a phone as on a desktop and in every
// theme (skill .claude/skills/buttons-and-spacing):
// - buttons side by side keep a gap between them (they stuck together in the card's
//   header and in a case's «Финансы» on a phone);
// - a button has a fill of its own, not the colour of the card or the page under it
//   (on the dark theme they were empty black boxes);
// - «Удалить карточку» is red before it is touched: a phone has no hover;
// - «Написать в Telegram» is filled with Telegram's blue in every theme;
// - a button beside an input («Сгенерировать» of the vault) stays in the input's column.
// Both languages; the theme is switched in place, so all four are checked on one load.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

const THEMES = ["blue", "dark", "warm", "minimal"] as const;
const WIDTHS = [390, 1280];
// The gap of a row of buttons (.actions, .form-actions, .card-head-actions in styles.css).
const MIN_GAP = 8;
// A password as «Сгенерировать» makes one: 12 characters is the default length of a site.
const TWELVE_CHARACTERS = "k7#Rm2@xQ9p!";

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Card = { can_delete: boolean; cases: unknown[] | null };

type Found = { id: string; telegram: string | null };

/** A card with «Удалить карточку» (the specialist may delete any), a card with a case
    and its money, and a card whose Telegram is a username, so it shows «Написать в
    Telegram». */
async function demoCards(
  page: Page,
): Promise<{ plain: string; withCase: string; chat: string }> {
  const found = await page.request.post("/api/contacts/search", { data: {} });
  expect(found.ok()).toBe(true);
  let plain = "";
  let withCase = "";
  let chat = "";
  // Oldest first: the demo's own cards, not the ones other tests make and delete meanwhile.
  for (const { id, telegram } of ((await found.json()).items as Found[]).reverse()) {
    // The rule of telegramUrl in src/contacts.ts.
    if (!chat && telegram && /^[A-Za-z0-9_]{5,32}$/.test(telegram)) chat = id;
    const card = (await (await page.request.get(`/api/contacts/${id}`)).json()) as Card;
    if (!plain && card.can_delete) plain = id;
    if (!withCase && card.cases?.length) withCase = id;
    if (plain && withCase && chat) return { plain, withCase, chat };
  }
  throw new Error("The demo has no deletable card, card with a case or with a Telegram");
}

/** Buttons of the page (not the menu) that sit in one row closer than MIN_GAP. */
function stuckButtons(page: Page): Promise<string[]> {
  return page.evaluate((minGap) => {
    const boxes = [...document.querySelectorAll<HTMLElement>(".page .btn")]
      .filter((b) => b.offsetParent !== null)
      .map((b) => ({ label: b.textContent?.trim() ?? "", r: b.getBoundingClientRect() }));
    const stuck: string[] = [];
    for (const a of boxes) {
      for (const b of boxes) {
        const sameRow = a.r.top < b.r.bottom - 1 && b.r.top < a.r.bottom - 1;
        const gap = b.r.left - a.r.right;
        if (a !== b && sameRow && b.r.left >= a.r.left && gap < minGap - 0.5) {
          stuck.push(`«${a.label}» + «${b.label}»: ${gap.toFixed(1)}px`);
        }
      }
    }
    return stuck;
  }, MIN_GAP);
}

/** Buttons whose fill is the colour of what is behind them. */
function unfilledButtons(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const opaque = (color: string) => color !== "transparent" && !/,\s*0\)$/.test(color);
    const backdrop = (el: HTMLElement): string => {
      for (let p = el.parentElement; p; p = p.parentElement) {
        const color = getComputedStyle(p).backgroundColor;
        if (opaque(color)) return color;
      }
      return getComputedStyle(document.body).backgroundColor;
    };
    return [...document.querySelectorAll<HTMLElement>(".page .btn")]
      .filter((b) => b.offsetParent !== null)
      .filter((b) => getComputedStyle(b).backgroundColor === backdrop(b))
      .map((b) => b.textContent?.trim() ?? "");
  });
}

/** A colour variable of styles.css (`--c-red`), as the browser computes it. */
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

/** Buttons beside an input (`.input-action`: «Сгенерировать», «Добавить город») that do
    not stay in their own column: past the field or the card, over another control, or
    cut. Also a page that has become wider than the window because of such a row. */
function spilledInputButtons(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const shown = (el: Element) => (el as HTMLElement).offsetParent !== null;
    const controls = [
      ...document.querySelectorAll<HTMLElement>(".page input, .page select, .page textarea"),
    ].filter(shown);
    const spilled: string[] = [];
    for (const row of document.querySelectorAll<HTMLElement>(".page .input-action")) {
      if (!shown(row)) continue;
      // The column of a form grid; outside one (the cities of a direction), the card.
      const column = (row.closest(".field") ?? row.closest(".card"))!.getBoundingClientRect();
      for (const button of row.querySelectorAll<HTMLElement>(".btn")) {
        const r = button.getBoundingClientRect();
        const label = `«${button.textContent?.trim()}»`;
        if (r.left < column.left - 0.5 || r.right > column.right + 0.5) {
          spilled.push(
            `${label} at ${r.left.toFixed(0)}–${r.right.toFixed(0)}px, ` +
              `its column ${column.left.toFixed(0)}–${column.right.toFixed(0)}px`,
          );
        }
        if (button.scrollWidth > button.clientWidth) {
          spilled.push(`${label} is cut: ${button.scrollWidth} > ${button.clientWidth}px`);
        }
        for (const control of controls) {
          if (row.contains(control)) continue;
          const c = control.getBoundingClientRect();
          const apart =
            r.right <= c.left + 0.5 ||
            c.right <= r.left + 0.5 ||
            r.bottom <= c.top + 0.5 ||
            c.bottom <= r.top + 0.5;
          if (!apart) {
            const name = control.closest(".field")?.firstElementChild?.textContent?.trim();
            spilled.push(`${label} covers the field «${name ?? control.tagName}»`);
          }
        }
      }
    }
    const root = document.documentElement;
    if (root.scrollWidth > root.clientWidth) {
      spilled.push(`the page is wider than the window: ${root.scrollWidth}px`);
    }
    return spilled;
  });
}

/** The oldest demo card with a case: its vault form offers the cases too. */
async function demoCardWithCase(page: Page): Promise<string> {
  const found = await page.request.post("/api/contacts/search", { data: { query: "demo_" } });
  expect(found.ok()).toBe(true);
  for (const { id } of ((await found.json()).items as Found[]).reverse()) {
    const card = (await (await page.request.get(`/api/contacts/${id}`)).json()) as Card;
    if (card.cases?.length) return id;
  }
  throw new Error("The demo has no card with a case");
}

// A button beside an input stays in the input's column (backlog 1.15.10): on the card
// «Сгенерировать» of the vault's form ran into the next column and «Доп. идентификатор»
// covered it; at 1440px the card's form has two columns, as on the specialist's screen.
for (const lang of ["ru", "en"] as const) {
  for (const width of [...WIDTHS, 1440]) {
    test(`a field's button stays in its column (${lang}, ${width}px)`, async ({ page }) => {
      const t = (key: string) => {
        if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
        return LOCALES[lang][key];
      };
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      await page.setViewportSize({ width, height: 900 });
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
      const client = await demoCardWithCase(page);

      const vaultCard = page
        .locator("section.card")
        .filter({ has: page.getByRole("heading", { name: t("vault.title"), exact: true }) });
      const screens = [
        {
          name: "the vault's form on a client's card",
          open: async () => {
            await page.goto(`/app/clients/${client}`);
            await vaultCard.getByRole("button", { name: t("vault.add"), exact: true }).click();
          },
        },
        {
          name: "a new own account on «Сейф»",
          open: async () => {
            await page.goto("/app/vault");
            await page.getByRole("button", { name: t("vault.addOwn"), exact: true }).click();
          },
        },
        {
          name: "the cities of a new direction",
          open: async () => {
            await page.goto("/app/countries");
            await page.getByRole("button", { name: t("directions.add"), exact: true }).click();
          },
        },
      ];
      for (const screen of screens) {
        await screen.open();
        const row = page.locator(".input-action").first();
        await expect(row).toBeVisible();
        const generate = row.getByRole("button", { name: t("vault.generate"), exact: true });
        const password = (await generate.count()) > 0 ? row.locator("input") : null;
        // Typed, not generated: nothing is asked of the server and the text is the same
        // every run. The demo's systems want 12 characters.
        await password?.fill(TWELVE_CHARACTERS);
        for (const theme of THEMES) {
          await page.evaluate((th) => (document.documentElement.dataset.uiTheme = th), theme);
          const where = `${theme}, ${lang}, ${width}px`;
          expect
            .soft(
              await spilledInputButtons(page),
              `${screen.name}: the button stays beside or under its input (${where})`,
            )
            .toEqual([]);
          // The input keeps room for a password: it is seen whole, without scrolling.
          if (password) {
            expect
              .soft(
                await password.evaluate((el) => el.scrollWidth <= el.clientWidth),
                `${screen.name}: a password of 12 characters fits its field (${where})`,
              )
              .toBe(true);
          }
        }
      }
    });
  }
}

for (const lang of ["ru", "en"] as const) {
  for (const width of WIDTHS) {
    test(`client's card: buttons stand apart and read as buttons (${lang}, ${width}px)`, async ({
      page,
    }) => {
      const t = (key: string) => {
        if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
        return LOCALES[lang][key];
      };
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      await page.setViewportSize({ width, height: 900 });
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
      const { plain, withCase, chat } = await demoCards(page);
      const header = page.locator(".page-header");
      const writeTelegram = header.getByRole("link", {
        name: t("contacts.writeTelegram"),
        exact: true,
      });

      for (const [id, ready] of [
        [plain, page.getByRole("button", { name: t("contacts.deleteCard"), exact: true })],
        [withCase, page.getByRole("button", { name: t("finance.addPayment"), exact: true })],
        [chat, writeTelegram],
      ] as const) {
        await page.goto(`/app/clients/${id}`);
        await expect(ready).toBeVisible();
        await expect(page.getByRole("heading", { name: t("contacts.timeline") })).toBeVisible();

        for (const theme of THEMES) {
          await page.evaluate((th) => (document.documentElement.dataset.uiTheme = th), theme);
          const where = `${theme}, ${lang}, ${width}px`;
          // Soft: one run lists every broken rule in every theme.
          expect
            .soft(await stuckButtons(page), `buttons side by side keep a gap (${where})`)
            .toEqual([]);
          expect
            .soft(
              await unfilledButtons(page),
              `a button has a fill of its own, not the card's or the page's (${where})`,
            )
            .toEqual([]);
          if (id === plain) {
            const remove = header.getByRole("button", {
              name: t("contacts.deleteCard"),
              exact: true,
            });
            // The colour is there as soon as the theme is set; a short wait keeps a red run quick.
            await expect
              .soft(remove, `«Удалить карточку» is red untouched (${where})`)
              .toHaveCSS("color", await themeColor(page, "--c-red"), { timeout: 1000 });
          }
          if (id === chat) {
            await expect
              .soft(writeTelegram, `«Написать в Telegram» is Telegram's blue (${where})`)
              .toHaveCSS("background-color", await themeColor(page, "--c-telegram"), {
                timeout: 1000,
              });
          }
        }
      }
    });
  }
}

import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// Buttons on a client's card read as buttons, on a phone as on a desktop and in every
// theme (skill .claude/skills/buttons-and-spacing):
// - buttons side by side keep a gap between them (they stuck together in the card's
//   header and in a case's «Финансы» on a phone);
// - a button has a fill of its own, not the colour of the card or the page under it
//   (on the dark theme they were empty black boxes);
// - «Удалить карточку» is red before it is touched: a phone has no hover.
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

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Card = { can_delete: boolean; cases: unknown[] | null };

/** A card that can still be deleted (no history) and a card with a case and its money. */
async function demoCards(page: Page): Promise<{ plain: string; withCase: string }> {
  const found = await page.request.post("/api/contacts/search", { data: {} });
  expect(found.ok()).toBe(true);
  let plain = "";
  let withCase = "";
  for (const { id } of (await found.json()).items as { id: string }[]) {
    const card = (await (await page.request.get(`/api/contacts/${id}`)).json()) as Card;
    if (!plain && card.can_delete) plain = id;
    if (!withCase && card.cases?.length) withCase = id;
    if (plain && withCase) return { plain, withCase };
  }
  throw new Error("The demo has no card without history or no card with a case");
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

/** The theme's red, as the browser computes it. */
function themeRed(page: Page): Promise<string> {
  return page.evaluate(() => {
    const probe = document.createElement("span");
    probe.style.color = "var(--c-red)";
    document.body.append(probe);
    const red = getComputedStyle(probe).color;
    probe.remove();
    return red;
  });
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
      const { plain, withCase } = await demoCards(page);

      for (const [id, ready] of [
        [plain, t("contacts.deleteCard")],
        [withCase, t("finance.addPayment")],
      ]) {
        await page.goto(`/app/clients/${id}`);
        await expect(page.getByRole("button", { name: ready, exact: true })).toBeVisible();
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
            const remove = page
              .locator(".page-header")
              .getByRole("button", { name: t("contacts.deleteCard"), exact: true });
            // The colour is there as soon as the theme is set; a short wait keeps a red run quick.
            await expect
              .soft(remove, `«Удалить карточку» is red untouched (${where})`)
              .toHaveCSS("color", await themeRed(page), { timeout: 1000 });
          }
        }
      }
    });
  }
}

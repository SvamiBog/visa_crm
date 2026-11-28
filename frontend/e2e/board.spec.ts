import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The kanban of the client index (backlog 1.5): a card dragged to another column asks
// first in the site's window, because it is easy to drop one by mistake. A case moves to
// the new stage only after «Перенести»; a client from «Консультация» dropped on a stage
// opens the case form in the client's card after «Открыть кейс»; a case without a result
// dropped on «Результат» stays and, after «Внести итог», opens its form at the result
// (CRM audit 2026-11-18). Both languages; each takes its own cards, since the tests run
// in parallel.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

type Stage = "waiting" | "documents" | "submission" | "review" | "result";
type BoardCase = {
  id: string;
  contact_id: string;
  full_name: string;
  group_id: string | null;
  stage: Stage;
  outcome: string | null;
};
type BoardLead = { contact_id: string; full_name: string; held_on: string | null };
type Board = { cases: BoardCase[]; leads: BoardLead[] };

async function board(page: Page): Promise<Board> {
  const response = await page.request.get("/api/cases/board");
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as Board;
}

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** The title of a board column (it ends with the number of cards). */
function title(page: Page, name: string) {
  return page.getByRole("heading", { name: new RegExp(`^${escape(name)}\\s`) });
}

function column(page: Page, name: string) {
  return page.locator(".board-col").filter({ has: title(page, name) });
}

function boardCard(page: Page, name: string) {
  return page
    .locator("article.board-card")
    .filter({ has: page.getByRole("link", { name, exact: true }) });
}

/** Drags a card onto a column's title. Both must be on screen: `dragTo` scrolls to the
 * target after pressing the mouse on the card, and a scroll in between drags whatever
 * card is under the mouse then. */
async function drop(page: Page, card: Locator, stage: string): Promise<void> {
  const target = title(page, stage);
  await expect(card).toBeInViewport({ ratio: 1 });
  await expect(target).toBeInViewport({ ratio: 1 });
  await card.dragTo(target);
}

/** Answers the site's question with the button of that name; it closes. */
async function answer(page: Page, button: string): Promise<void> {
  const ask = page.getByRole("dialog");
  await ask.getByRole("button", { name: button, exact: true }).click();
  await expect(ask).toBeHidden();
}

// The whole demo board fits on a wide screen, so nothing scrolls during a drag.
test.use({ viewport: { width: 1920, height: 1200 } });

for (const [which, lang] of (["ru", "en"] as const).entries()) {
  test.describe(`board in ${lang}`, () => {
    const t = (key: string) => {
      if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
      return LOCALES[lang][key];
    };

    test.beforeEach(async ({ page }) => {
      expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      const login = await page.request.post("/api/auth/login", {
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(login.ok(), await login.text()).toBe(true);
    });

    test("a case moves to another stage only after the confirmation", async ({ page }) => {
      const single = (await board(page)).cases.filter(
        (c) => c.group_id === null && c.stage !== "result",
      );
      const moved = single[which];
      expect(moved, "the demo has too few single cases in work").toBeTruthy();
      const target: Stage = moved.stage === "waiting" ? "documents" : "waiting";
      const from = t(`cases.stage.${moved.stage}`);
      const to = t(`cases.stage.${target}`);

      await page.goto("/app/clients?view=board");
      const card = boardCard(page, moved.full_name);
      await expect(column(page, from).locator(card)).toBeVisible();
      try {
        await drop(page, card, to);
        for (const part of [moved.full_name, from, to]) {
          await expect(page.getByRole("dialog")).toContainText(part);
        }
        await answer(page, t("common.cancel"));
        await expect(column(page, from).locator(card), "«Cancel» leaves it").toBeVisible();
        expect((await board(page)).cases.find((c) => c.id === moved.id)?.stage).toBe(
          moved.stage,
        );

        await drop(page, card, to);
        await answer(page, t("confirm.move"));
        await expect(column(page, to).locator(card)).toBeVisible();
        await expect
          .poll(async () => (await board(page)).cases.find((c) => c.id === moved.id)?.stage)
          .toBe(target);
      } finally {
        // The demo stays as it was for the other tests.
        await page.request.post("/api/cases/stage", {
          data: { case_ids: [moved.id], stage: moved.stage },
        });
      }
    });

    test("a case without a result dropped on «Результат» opens its form at the result", async ({
      page,
    }) => {
      // Cards of their own: the test above moves the first two single cases.
      const open = (await board(page)).cases.filter(
        (c) => c.group_id === null && c.stage !== "result" && c.outcome === null,
      );
      const kept = open[which + 2];
      expect(kept, "the demo has too few single cases in work").toBeTruthy();
      const from = t(`cases.stage.${kept.stage}`);
      const result = t("cases.stage.result");

      await page.goto("/app/clients?view=board");
      const card = boardCard(page, kept.full_name);
      await expect(column(page, from).locator(card)).toBeVisible();
      // «Результат» is the last column: the board scrolls sideways to bring it in.
      await title(page, result).scrollIntoViewIfNeeded();
      await drop(page, card, result);
      await expect(page.getByRole("dialog")).toContainText(kept.full_name);
      await answer(page, t("common.cancel"));
      await expect(column(page, from).locator(card), "the case stays without a result").toBeVisible();

      await drop(page, card, result);
      await answer(page, t("confirm.enterResult"));
      // The card opens on «Кейсы» (backlog 1.15.17).
      await expect(page).toHaveURL(new RegExp(`/app/clients/${kept.contact_id}\\?tab=cases$`));
      await expect(page.getByText(t("cases.resultNeedsOutcome"))).toBeVisible();
      await expect(page.getByRole("combobox", { name: t("cases.outcomeLabel") })).toBeFocused();
      expect((await board(page)).cases.find((c) => c.id === kept.id)?.stage).toBe(kept.stage);
    });

    test("a client from «Консультация» dropped on a stage opens the case form", async ({
      page,
    }) => {
      const lead = (await board(page)).leads.filter((l) => l.held_on)[which];
      expect(lead, "the demo has too few clients after a consultation").toBeTruthy();

      await page.goto("/app/clients?view=board");
      const card = boardCard(page, lead.full_name);
      const waiting = t("cases.stage.waiting");

      await drop(page, card, waiting);
      await expect(page.getByRole("dialog")).toContainText(lead.full_name);
      await answer(page, t("common.cancel"));
      await expect(card, "«Cancel» stays on the board").toBeVisible();

      await drop(page, card, waiting);
      await answer(page, t("confirm.openCase"));
      // The card opens on «Кейсы» (backlog 1.15.17).
      await expect(page).toHaveURL(new RegExp(`/app/clients/${lead.contact_id}\\?tab=cases$`));
      await expect(
        page.getByRole("heading", { name: t("cases.createFromConsultation"), exact: true }),
      ).toBeVisible();
    });

    test("«Результат» shows the results of the chosen period and remembers it", async ({
      page,
    }) => {
      await page.goto("/app/clients?view=board");
      const result = t("cases.stage.result");
      const periods = page.getByRole("group", { name: t("index.resultPeriod"), exact: true });
      const choice = (period: string) =>
        periods.getByRole("button", { name: t(`index.resultPeriods.${period}`), exact: true });
      const from = page.getByLabel(t("index.resultFrom"), { exact: true });
      const to = page.getByLabel(t("index.resultTo"), { exact: true });
      await expect(choice("month")).toHaveAttribute("aria-pressed", "true");
      await expect(from, "own dates open with «Период» only").toHaveCount(0);

      /** Does what changes the period and checks «Результат» against the board it asked for. */
      const shows = async (asked: Record<string, string>, change: () => Promise<void>) => {
        const response = page.waitForResponse((r) => {
          const url = new URL(r.url());
          return (
            url.pathname === "/api/cases/board" &&
            Object.entries(asked).every(([key, value]) => url.searchParams.get(key) === value)
          );
        });
        await change();
        const data = (await (await response).json()) as Board;
        const cards = new Set(
          data.cases.filter((c) => c.stage === "result").map((c) => c.group_id ?? c.id),
        );
        await expect(title(page, result)).toHaveText(new RegExp(`\\s${cards.size}$`));
      };

      await shows({ result_period: "year" }, () => choice("year").click());
      await shows({ result_period: "all" }, () => choice("all").click());
      await expect(choice("all")).toHaveAttribute("aria-pressed", "true");

      // Own dates: both ends go in order whichever was typed first.
      await choice("dates").click();
      await shows({ result_period: "dates", result_from: "2025-01-01" }, () =>
        from.fill("2025-01-01"),
      );
      await shows({ result_from: "2024-12-31", result_to: "2025-01-01" }, () =>
        to.fill("2024-12-31"),
      );

      await page.reload();
      await expect(choice("dates"), "the choice stays after a reload").toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await expect(from).toHaveValue("2025-01-01");
      await expect(to).toHaveValue("2024-12-31");
    });

    test("the search finds a card by the words of the name in any order", async ({ page }) => {
      // Backlog A2: «Имя Фамилия», as a messenger shows a name, finds the card kept
      // surname first, as the list's search does.
      const named = (await board(page)).cases.find(
        (c) => c.stage !== "result" && c.full_name.split(" ").length >= 2,
      );
      expect(named, "the demo has no case in work with a name of two words").toBeTruthy();
      const [surname, given] = named!.full_name.split(" ");
      expect(given, "the demo's names read the same both ways").not.toBe(surname);

      await page.goto("/app/clients?view=board");
      const card = boardCard(page, named!.full_name).first();
      await expect(card).toBeVisible();
      const search = page.getByRole("searchbox", { name: t("contacts.search"), exact: true });
      await search.fill(`${given}   ${surname.toLocaleUpperCase()}`);
      await expect(card, "the given name first, another case").toBeVisible();
      await search.fill(`${given} ${surname} nobody-is-called-so`);
      await expect(card, "every word must be in the name").toHaveCount(0);
    });
  });
}

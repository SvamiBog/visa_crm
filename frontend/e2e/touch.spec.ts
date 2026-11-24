import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// How the site answers a finger (decisions «Отклик на нажатие»):
// - on a touch screen no text field is under 16px: a phone zooms the whole page into such
//   a field and leaves it zoomed (the fields were 14px, the body's size);
// - a pressed button gives way a little at once and comes back when let go; with «reduce
//   motion» set on the device it only changes colour.
// Both languages, a phone's touch screen and a desktop's mouse.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The size under which a phone zooms into a focused field.
const MIN_FIELD_PX = 16;
const PHONE = { width: 390, height: 844 };

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

/** Text fields on the screen whose letters are smaller than MIN_FIELD_PX. */
function smallFields(page: Page): Promise<string[]> {
  return page.evaluate((min) => {
    const notText = ["checkbox", "radio", "file", "hidden", "range", "color", "submit", "button"];
    return [...document.querySelectorAll<HTMLElement>("input, select, textarea")]
      .filter((el) => el.offsetParent !== null)
      .filter((el) => !(el instanceof HTMLInputElement && notText.includes(el.type)))
      .map((el) => ({ el, size: parseFloat(getComputedStyle(el).fontSize) }))
      .filter(({ size }) => size < min)
      .map(({ el, size }) => {
        const label = el.closest("label")?.textContent?.trim().slice(0, 40) ?? "";
        return `${el.tagName.toLowerCase()} «${label}»: ${size}px`;
      });
  }, MIN_FIELD_PX);
}

/** Starts the press transition and tells whether one runs and where it ends. */
function pressState(page: Page, selector: string) {
  return page.locator(selector).first().evaluate((el) => {
    const transform = getComputedStyle(el).transform;
    return { transform, moving: el.getAnimations().length > 0 };
  });
}

for (const lang of ["ru", "en"] as const) {
  const t = (key: string) => {
    if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
    return LOCALES[lang][key];
  };

  test.describe(`a touch screen in ${lang}`, () => {
    test.use({ viewport: PHONE, hasTouch: true, isMobile: true });

    test.beforeEach(async ({ page }) => {
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
    });

    test("the login form does not zoom the page", async ({ page }) => {
      await page.goto("/login");
      await expect(page.locator("form input").first()).toBeVisible();
      const coarse = await page.evaluate(() => matchMedia("(pointer: coarse)").matches);
      expect(coarse, "the browser takes the finger for the pointer").toBe(true);
      expect(await smallFields(page)).toEqual([]);
    });

    const SCREENS: { name: string; open: (page: Page) => Promise<void> }[] = [
      { name: "the client index", open: async (page) => void (await page.goto("/app/clients")) },
      { name: "a new client", open: async (page) => void (await page.goto("/app/clients?new=1")) },
      {
        name: "a client's details",
        open: async (page) => {
          const found = await page.request.post("/api/contacts/search", { data: {} });
          // Oldest first: a demo card, not one other tests make and delete meanwhile.
          const [first] = ((await found.json()).items as { id: string }[]).reverse();
          await page.goto(`/app/clients/${first.id}`);
          await page.locator(".page-header").getByRole("button", { name: t("reference.edit") }).click();
        },
      },
      {
        name: "the stay calculator",
        open: async (page) => void (await page.goto("/app/stay-calculator")),
      },
      { name: "the dictionaries", open: async (page) => void (await page.goto("/app/dictionaries")) },
    ];

    for (const screen of SCREENS) {
      test(`${screen.name}: no field zooms the page`, async ({ page }) => {
        await logIn(page);
        await screen.open(page);
        await expect(page.locator(".page input, .page select").first()).toBeVisible();
        expect(await smallFields(page)).toEqual([]);
      });
    }
  });

  test.describe(`a pressed button in ${lang}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      await logIn(page);
    });

    test("gives way under the finger and comes back", async ({ page }) => {
      await page.goto("/app/clients");
      const button = page.locator(".page .btn").first();
      const box = await button.boundingBox();
      expect(box).not.toBeNull();
      await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
      await page.mouse.down();
      await expect
        .poll(async () => (await pressState(page, ".page .btn")).transform)
        .toBe("matrix(0.97, 0, 0, 0.97, 0, 0)");
      // Let go away from the button, so the press is not a click that leaves the page.
      await page.mouse.move(1, 1);
      await page.mouse.up();
      await expect.poll(async () => (await pressState(page, ".page .btn")).transform).toBe("none");
    });

    test("with reduced motion only changes colour", async ({ page }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.goto("/app/clients");
      const button = page.locator(".page .btn").first();
      const box = await button.boundingBox();
      expect(box).not.toBeNull();
      await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
      await page.mouse.down();
      expect(await pressState(page, ".page .btn")).toEqual({ transform: "none", moving: false });
      await page.mouse.move(1, 1);
      await page.mouse.up();
    });
  });
}

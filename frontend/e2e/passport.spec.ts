import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// «Распознать паспорт» (backlog 2.5) reads the photo in the browser: the zone is found on
// the photo, cut out level and read by the engine, and the API checks the lines. The photos
// in e2e/fixtures are drawn pages of invented people in the zone's font, as a messenger
// leaves them (1280 px, JPEG): a Belarusian passport of the old series, a biometric one
// photographed at a tilt and a Ukrainian one photographed sideways. Nothing is moved into
// the card.

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

// The demo specialist (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";

const PHOTOS = [
  {
    lang: "ru",
    file: "passport-by-1996.jpg",
    lines: [
      "P<BLRKAZLOUSKAYA<<VOLHA<<<<<<<<<<<<<<<<<<<<<",
      "MP34567893BLR8505051F29050404050585A001PB510",
    ],
    shown: ["KAZLOUSKAYA", "VOLHA", "MP3456789", "4050585A001PB5"],
  },
  {
    lang: "en",
    file: "passport-by-2021-tilted.jpg",
    lines: [
      "P<BLRNOVIK<<ALIAKSANDR<<<<<<<<<<<<<<<<<<<<<<",
      "BP01234567BLR7801011M32031453010178H023PB888",
    ],
    shown: ["NOVIK", "ALIAKSANDR", "BP0123456", "3010178H023PB8"],
  },
  {
    lang: "ru",
    file: "passport-ua-sideways.jpg",
    lines: [
      "P<UKRSHEVCHENKO<<OKSANA<<<<<<<<<<<<<<<<<<<<<",
      "FE123456<8UKR9103270F31091551991032701234<22",
    ],
    shown: ["SHEVCHENKO", "OKSANA", "FE123456", "1991032701234"],
  },
] as const;

for (const photo of PHOTOS) {
  test(`reads ${photo.file} in ${photo.lang}`, async ({ page }) => {
    const t = (key: string) => {
      if (!(key in LOCALES[photo.lang])) throw new Error(`No ${key} in ${photo.lang}.json`);
      return LOCALES[photo.lang][key];
    };
    expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
    await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), photo.lang);
    const login = await page.request.post("/api/auth/login", {
      data: { email: EMAIL, password: PASSWORD },
    });
    expect(login.ok(), await login.text()).toBe(true);
    const found = await page.request.post("/api/contacts/search", { data: { tab: "work" } });
    expect(found.ok()).toBe(true);
    const [client] = (await found.json()).items as { id: string }[];
    await page.goto(`/app/clients/${client.id}`);

    await page.getByRole("button", { name: t("mrz.open"), exact: true }).click();
    await page
      .getByLabel(t("mrz.photo"))
      .setInputFiles(fileURLToPath(new URL(`fixtures/${photo.file}`, import.meta.url)));

    // The engine and its model load on the first photo.
    await expect(page.getByLabel(t("mrz.lines"))).toHaveValue(photo.lines.join("\n"), {
      timeout: 30_000,
    });
    await expect(page.getByText(t("mrz.valid"), { exact: true })).toBeVisible();
    for (const value of photo.shown) {
      await expect(page.getByRole("cell", { name: value, exact: true }).first()).toBeVisible();
    }
  });
}

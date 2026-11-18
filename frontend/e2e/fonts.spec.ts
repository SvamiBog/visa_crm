import { expect, test } from "@playwright/test";

// The fonts of every theme come from the site itself (src/fonts.css, docs/decisions.md
// «Свои шрифты»): the page asks no other domain for anything, so a specialist's IP
// address does not reach Google, and the theme's own typefaces really load (a font
// file that went missing would leave the text in a system font without an error).
// The login page needs no account and shows both the text and the heading typefaces.

const THEME_FONTS = {
  blue: ["Onest"],
  dark: ["Inter"],
  warm: ["Manrope", "Lora"],
  minimal: ["IBM Plex Sans"],
} as const;

for (const [theme, families] of Object.entries(THEME_FONTS)) {
  test(`the ${theme} theme loads its fonts from the site itself`, async ({ page, baseURL }) => {
    const origin = new URL(baseURL!).origin;
    const elsewhere: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.protocol.startsWith("http") && url.origin !== origin) elsewhere.push(url.host);
    });
    await page.addInitScript((value) => localStorage.setItem("visacrm.theme", value), theme);

    await page.goto("/login");
    await expect(page.locator("html")).toHaveAttribute("data-ui-theme", theme);
    await page.waitForLoadState("networkidle");
    const loaded = await page.evaluate(async () => {
      await document.fonts.ready;
      return [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family);
    });

    for (const family of families) {
      expect(loaded.map((name) => name.replace(/"/g, ""))).toContain(family);
    }
    expect(elsewhere).toEqual([]);
  });
}

import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

// The password field of the sign-in screen has an eye button inside it (UI audit
// 17.11.2026, components/PasswordField.tsx): it shows and hides the password, says its
// state with aria-pressed, is a 44px target, has a visible focus ring, sits clear of the
// text in every theme, and sending the form hides the password again. Both languages, on
// a computer and on a phone. The field is found by its exact label: «Пароль» is also a
// part of the button's name «Показать пароль».

type Lang = "ru" | "en";

const LOCALES: Record<Lang, Record<string, string>> = {
  ru: JSON.parse(readFileSync(new URL("../src/locales/ru.json", import.meta.url), "utf8")),
  en: JSON.parse(readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8")),
};

const THEMES = ["blue", "dark", "warm", "minimal"] as const;
const WIDTHS = [1280, 390];
// No such account: a wrong try does not count towards any account's lockout.
const NOBODY = "nobody@visacrm.test";

for (const lang of ["ru", "en"] as const) {
  for (const width of WIDTHS) {
    test(`the sign-in password can be shown and hidden, ${lang}, ${width}px`, async ({
      page,
    }) => {
      const t = (key: string) => {
        if (!(key in LOCALES[lang])) throw new Error(`No ${key} in ${lang}.json`);
        return LOCALES[lang][key];
      };
      await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang);
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/login");

      const password = page.getByLabel(t("auth.password"), { exact: true });
      const toggle = page.getByRole("button", { name: t("auth.showPassword"), exact: true });
      await expect(password).toHaveAttribute("type", "password");
      await expect(password).toHaveAttribute("autocomplete", "current-password");
      await expect(toggle).toHaveAttribute("aria-pressed", "false");
      await expect(toggle).toHaveAttribute("title", t("auth.showPassword"));

      await password.fill("typed-secret-1");
      await toggle.click();
      await expect(password).toHaveAttribute("type", "text");
      await expect(password).toHaveValue("typed-secret-1");
      await expect(toggle).toHaveAttribute("aria-pressed", "true");
      await expect(toggle).toHaveAttribute("title", t("auth.hidePassword"));
      await toggle.click();
      await expect(password).toHaveAttribute("type", "password");
      await expect(toggle).toHaveAttribute("aria-pressed", "false");

      // A target for a finger, inside the field, with the text stopping before it.
      const box = await toggle.boundingBox();
      const field = await password.boundingBox();
      expect(box && field, "the button and the field are on the screen").toBeTruthy();
      expect(box!.width).toBeGreaterThanOrEqual(44);
      expect(box!.height).toBeGreaterThanOrEqual(44);
      expect(box!.x + box!.width).toBeLessThanOrEqual(field!.x + field!.width + 0.5);
      const textEnd = await password.evaluate(
        (el) => el.getBoundingClientRect().right - parseFloat(getComputedStyle(el).paddingRight),
      );
      expect(textEnd, "the text stops before the button").toBeLessThanOrEqual(box!.x + 0.5);

      // The keyboard reaches it, with a ring around it, and Space presses it.
      await password.focus();
      await page.keyboard.press("Tab");
      await expect(toggle).toBeFocused();
      expect(await toggle.evaluate((el) => getComputedStyle(el).outlineStyle)).toBe("solid");
      await page.keyboard.press("Space");
      await expect(toggle).toHaveAttribute("aria-pressed", "true");

      // Seen in every theme: the icon is not the colour of the field under it.
      for (const theme of THEMES) {
        await page.evaluate((th) => (document.documentElement.dataset.uiTheme = th), theme);
        await expect(toggle, theme).toBeVisible();
        const [icon, under] = await toggle.evaluate((el) => [
          getComputedStyle(el).color,
          getComputedStyle(el.previousElementSibling as HTMLElement).backgroundColor,
        ]);
        expect(icon, `the eye stands out from the field (${theme})`).not.toBe(under);
      }

      // Sending the form hides the password again.
      await page.getByLabel(t("auth.email"), { exact: true }).fill(NOBODY);
      await page.getByRole("button", { name: t("auth.signIn"), exact: true }).click();
      // The error is the password field's own, under it (field-errors.spec.ts).
      await expect(password).toHaveAccessibleDescription(t("errors.invalid_credentials"));
      await expect(password).toHaveAttribute("type", "password");
      await expect(toggle).toHaveAttribute("aria-pressed", "false");

      const fits = await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      );
      expect(fits, "no sideways scrolling").toBe(true);
    });
  }
}

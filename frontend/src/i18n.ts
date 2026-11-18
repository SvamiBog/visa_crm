import i18n from "i18next";
import { initReactI18next } from "react-i18next";

const STORAGE_KEY = "visacrm.lang";

export const LANGUAGES = ["ru", "en"] as const;
export type Language = (typeof LANGUAGES)[number];

// Every user-facing string goes through t(); keys live in src/locales/{ru,en}.json. Each
// language is a file of its own: a page fetches only the language it shows, and the other
// one when the user switches to it.
const STRINGS: Record<Language, () => Promise<{ default: Record<string, unknown> }>> = {
  ru: () => import("./locales/ru.json"),
  en: () => import("./locales/en.json"),
};

function savedLanguage(): Language {
  try {
    return localStorage.getItem(STORAGE_KEY) === "en" ? "en" : "ru";
  } catch {
    return "ru";
  }
}

i18n.on("languageChanged", (lng) => {
  document.documentElement.lang = lng;
  try {
    localStorage.setItem(STORAGE_KEY, lng);
  } catch {
    // private mode or blocked storage: the choice just isn't remembered
  }
});

/** Resolves once the saved language's strings are in: the app renders after it (main.tsx). */
export const i18nReady: Promise<void> = (async () => {
  const lng = savedLanguage();
  const { default: strings } = await STRINGS[lng]();
  await i18n.use(initReactI18next).init({
    resources: { [lng]: { translation: strings } },
    lng,
    fallbackLng: "ru",
    interpolation: { escapeValue: false },
  });
  document.documentElement.lang = i18n.language;
})();

/**
 * Switches the interface language once its strings are in, so no screen shows bare keys.
 * Use it instead of `i18n.changeLanguage`, which would switch before they arrive.
 */
export async function changeLanguage(lng: Language): Promise<void> {
  if (!i18n.hasResourceBundle(lng, "translation")) {
    const { default: strings } = await STRINGS[lng]();
    i18n.addResourceBundle(lng, "translation", strings);
  }
  await i18n.changeLanguage(lng);
}

export default i18n;

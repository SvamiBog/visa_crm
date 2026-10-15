import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import ru from "./locales/ru.json";

const STORAGE_KEY = "visacrm.lang";

function savedLanguage(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) === "en" ? "en" : "ru";
  } catch {
    return "ru";
  }
}

// Every user-facing string goes through t(); keys live in src/locales/{ru,en}.json.
void i18n.use(initReactI18next).init({
  resources: { ru: { translation: ru }, en: { translation: en } },
  lng: savedLanguage(),
  fallbackLng: "ru",
  interpolation: { escapeValue: false },
});

document.documentElement.lang = i18n.language;
i18n.on("languageChanged", (lng) => {
  document.documentElement.lang = lng;
  try {
    localStorage.setItem(STORAGE_KEY, lng);
  } catch {
    // private mode or blocked storage: the choice just isn't remembered
  }
});

export default i18n;

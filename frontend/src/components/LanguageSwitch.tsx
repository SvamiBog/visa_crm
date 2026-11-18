import { useTranslation } from "react-i18next";
import { changeLanguage } from "../i18n";

export function LanguageSwitch({ className }: { className?: string }) {
  const { t, i18n } = useTranslation();
  return (
    <button
      type="button"
      className={className}
      onClick={() => void changeLanguage(i18n.language === "ru" ? "en" : "ru")}
    >
      {t("lang.switch")}
    </button>
  );
}

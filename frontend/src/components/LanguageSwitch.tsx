import { useTranslation } from "react-i18next";

export function LanguageSwitch({ className }: { className?: string }) {
  const { t, i18n } = useTranslation();
  return (
    <button
      type="button"
      className={className}
      onClick={() => void i18n.changeLanguage(i18n.language === "ru" ? "en" : "ru")}
    >
      {t("lang.switch")}
    </button>
  );
}

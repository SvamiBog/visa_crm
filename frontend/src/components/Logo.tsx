import { useTranslation } from "react-i18next";

export function Logo({ inverted = false }: { inverted?: boolean }) {
  const { t } = useTranslation();
  return (
    <div className={inverted ? "logo logo-inverted" : "logo"}>
      <span className="logo-mark" aria-hidden="true">
        V
      </span>
      {t("app.title")}
    </div>
  );
}

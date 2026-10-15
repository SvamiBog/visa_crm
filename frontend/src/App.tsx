import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiGet } from "./api";

type Health = { status: string; env: string };

export default function App() {
  const { t, i18n } = useTranslation();
  const health = useQuery({ queryKey: ["health"], queryFn: () => apiGet<Health>("/health") });

  const status = health.isPending
    ? t("health.checking")
    : health.isError
      ? t("health.error")
      : `${t("health.ok")} · ${health.data.env}`;

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: 32 }}>
      <h1>{t("app.title")}</h1>
      <p>{status}</p>
      <button onClick={() => void i18n.changeLanguage(i18n.language === "ru" ? "en" : "ru")}>
        {t("lang.switch")}
      </button>
    </main>
  );
}

import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiGet } from "../../api";

type Workspace = { name: string; base_currency: string; timezone: string };

export function HomePage() {
  const { t } = useTranslation();
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<Workspace>("/workspace"),
  });

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("cabinet.homeTitle")}</h1>
          {workspace.data && <div className="muted">{workspace.data.name}</div>}
        </div>
      </header>

      <section className="card">
        <h2 className="h2">{t("cabinet.comingTitle")}</h2>
        <p>{t("cabinet.comingText")}</p>
        {workspace.data && (
          <dl className="facts">
            <dt>{t("cabinet.baseCurrency")}</dt>
            <dd>{workspace.data.base_currency}</dd>
            <dt>{t("cabinet.timezone")}</dt>
            <dd>{workspace.data.timezone}</dd>
          </dl>
        )}
      </section>
    </div>
  );
}

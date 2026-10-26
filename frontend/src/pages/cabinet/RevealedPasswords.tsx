import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiGet } from "../../api";
import { ErrorAlert } from "../clients/ui";

// Letting an employee go (backlog Т.15): the vault passwords they revealed, from the
// audit log, and whether each was changed since — the rest are to change.

type RevealedPassword = {
  entry_id: string;
  system_name: string | null;
  login: string | null;
  contact_id: string | null;
  contact_name: string | null;
  times: number;
  last_revealed_at: string;
  changed_since: boolean;
  deleted: boolean;
};

export function RevealedPasswords({
  employee,
  onClose,
}: {
  employee: { id: string; email: string; is_active: boolean };
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const list = useQuery({
    queryKey: ["employees", employee.id, "revealed-passwords"],
    queryFn: () => apiGet<RevealedPassword[]>(`/employees/${employee.id}/revealed-passwords`),
  });
  const when = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium" });
  const left = list.data?.filter((r) => !r.changed_since && !r.deleted).length ?? 0;

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="h2">{t("revealed.title", { email: employee.email })}</h2>
        <button type="button" className="btn btn-sm" onClick={onClose}>
          {t("revealed.close")}
        </button>
      </div>
      <p className="muted small">
        {t(employee.is_active ? "revealed.hint" : "revealed.hintBlocked")}
      </p>
      {list.isPending && <p className="muted">{t("common.loading")}</p>}
      {list.isError && <ErrorAlert error={list.error} />}
      {list.data?.length === 0 && <p className="muted">{t("revealed.none")}</p>}
      {list.data && list.data.length > 0 && (
        <>
          <p>{t("revealed.left", { count: left })}</p>
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("revealed.colAccount")}</th>
                  <th>{t("revealed.colClient")}</th>
                  <th>{t("revealed.colLast")}</th>
                  <th>{t("admin.colStatus")}</th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((r) => (
                  <tr key={r.entry_id}>
                    <td>
                      {r.system_name}
                      {r.login && <div className="muted small">{r.login}</div>}
                    </td>
                    <td>
                      {r.contact_id ? (
                        <Link to={`/app/clients/${r.contact_id}`}>{r.contact_name}</Link>
                      ) : r.deleted ? (
                        "—"
                      ) : (
                        <Link to="/app/vault">{t("revealed.own")}</Link>
                      )}
                    </td>
                    <td className="muted">
                      {when.format(new Date(r.last_revealed_at))}
                      {r.times > 1 && ` · ${t("revealed.times", { count: r.times })}`}
                    </td>
                    <td>
                      {r.deleted ? (
                        <span className="chip st-gray">{t("revealed.deleted")}</span>
                      ) : r.changed_since ? (
                        <span className="chip st-green">{t("revealed.changed")}</span>
                      ) : (
                        <span className="chip st-orange">{t("revealed.toChange")}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

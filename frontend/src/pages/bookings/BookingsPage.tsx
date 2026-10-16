import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiGet } from "../../api";
import { formatDate, type Booking, type CaseStage } from "../../contacts";
import { byId, useCountries, useLocalName } from "../../reference";
import { DeadlineChip, useBookingAccounts, useStay } from "../clients/Bookings";
import { CaseChip } from "../clients/Cases";
import { ErrorAlert } from "../clients/ui";

// «Брони: дедлайны» (backlog 1.8): every live booking of every client, nearest
// cancellation date (or flight validity) first.

type DeadlineRow = Booking & {
  contact_id: string;
  contact_name: string;
  case_number: number;
  destination_id: string;
  stage: CaseStage;
};

export function BookingsPage() {
  const { t, i18n } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const stay = useStay();
  // The specialist's own accounts (Booking): a client's account is named only on the card.
  const accounts = useBookingAccounts(null);
  const rows = useQuery({
    queryKey: ["bookings", "deadlines"],
    queryFn: () => apiGet<DeadlineRow[]>("/bookings/deadlines"),
  });
  const [country, setCountry] = useState("");
  const destinations = useMemo(
    () => [...new Set((rows.data ?? []).map((r) => r.destination_id))],
    [rows.data],
  );
  const shown = (rows.data ?? []).filter((r) => !country || r.destination_id === country);

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("bookings.pageTitle")}</h1>
          <div className="muted">{t("bookings.pageSubtitle")}</div>
        </div>
      </header>

      {destinations.length > 1 && (
        <div className="filter-bar">
          <label className="field">
            <span>{t("index.country")}</span>
            <select value={country} onChange={(e) => setCountry(e.target.value)}>
              <option value="">{t("index.any")}</option>
              {destinations.map((id) => (
                <option key={id} value={id}>
                  {name.country(countryById.get(id))}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      <section className="card">
        {rows.isPending && <p className="muted">{t("common.loading")}</p>}
        {rows.isError && <ErrorAlert error={rows.error} />}
        {rows.isSuccess && shown.length === 0 && (
          <p className="muted">{t("bookings.noneLive")}</p>
        )}
        {shown.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("bookings.colDeadline")}</th>
                  <th>{t("bookings.colClient")}</th>
                  <th>{t("bookings.colBooking")}</th>
                  <th>{t("bookings.colDates")}</th>
                  <th>{t("bookings.account")}</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.id}>
                    <td className="nowrap">
                      {r.deadline ? (
                        <>
                          <div>{formatDate(r.deadline, i18n.language)}</div>
                          <DeadlineChip days={r.days_left} />
                        </>
                      ) : (
                        <span className="muted">{t("bookings.noDeadline")}</span>
                      )}
                    </td>
                    <td>
                      <Link to={`/app/clients/${r.contact_id}`}>{r.contact_name}</Link>
                      <div className="muted small">
                        {t("cases.number", { number: r.case_number })} ·{" "}
                        {name.country(countryById.get(r.destination_id))}
                      </div>
                      <CaseChip stage={r.stage} />
                    </td>
                    <td>
                      {r.place}
                      <div className="muted small">
                        {[
                          t(`bookings.kind.${r.kind}`),
                          r.status === "rescheduled" && t("bookings.status.rescheduled"),
                          r.reference && t("bookings.referenceValue", { reference: r.reference }),
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </div>
                      {r.warnings.map((w) => (
                        <div key={w} className="small warn-text">
                          {t(`bookings.warning.${w}`)}
                        </div>
                      ))}
                    </td>
                    <td className="nowrap muted">{stay(r) ?? "—"}</td>
                    <td className="muted">
                      {r.vault_entry_id
                        ? (accounts.labels.get(r.vault_entry_id) ?? t("bookings.accountInVault"))
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

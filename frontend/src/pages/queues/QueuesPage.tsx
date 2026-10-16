import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiGet } from "../../api";
import {
  formatDate,
  useCaseLists,
  type Attempt,
  type CaseStage,
  type EmailOwner,
  type Hall,
  type QueueEntry,
} from "../../contacts";
import { byId, useCountries, useLocalName, useRules, type VisaCenter } from "../../reference";
import { ATTEMPT_CHIP, QUEUE_CHIP } from "../clients/Booking";
import { CaseChip } from "../clients/Cases";

// «Очередь: страна» (backlog 1.6): who waits since when under what number, the
// applications with their deadlines, and the average wait by city and purpose.

type QueueRow = QueueEntry & {
  number: number;
  contact_id: string;
  full_name: string;
  vac_number: string | null;
  application_email: string | null;
  application_email_owner: EmailOwner | null;
  visa_center_id: string | null;
  purpose_id: string | null;
  registrar_id: string | null;
  hall: Hall | null;
  stage: CaseStage;
  submission_on: string | null;
};

type AttemptRow = Attempt & {
  number_of_case: number;
  contact_id: string;
  full_name: string;
  vac_number: string | null;
  visa_center_id: string | null;
  purpose_id: string | null;
  stage: CaseStage;
  submission_on: string | null;
};

type QueueView = {
  entries: QueueRow[];
  attempts: AttemptRow[];
  averages: { visa_center_id: string | null; purpose_id: string | null; average_days: number; entries: number }[];
};

type QueueCountry = { country_id: string; waiting: number; dated: number; open_attempts: number };

export function QueuesPage() {
  const { t, i18n } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const rules = useRules(false);
  const centerById = useMemo(() => {
    const centers = new Map<string, VisaCenter>();
    for (const rule of rules.data ?? []) for (const v of rule.visa_centers) centers.set(v.id, v);
    return centers;
  }, [rules.data]);
  const lists = useCaseLists();
  const purposeById = useMemo(() => byId(lists.purposes), [lists.purposes]);
  const registrarById = useMemo(() => byId(lists.registrars), [lists.registrars]);
  const summary = useQuery({
    queryKey: ["queues", "summary"],
    queryFn: () => apiGet<QueueCountry[]>("/queues/summary"),
  });
  const [picked, setPicked] = useState("");
  const [closed, setClosed] = useState(false);
  const country = picked || summary.data?.[0]?.country_id || "";
  const view = useQuery({
    queryKey: ["queues", country, closed],
    queryFn: () =>
      apiGet<QueueView>(`/queues?country_id=${country}${closed ? "&closed=true" : ""}`),
    enabled: Boolean(country),
  });
  const date = (value: string | null) => formatDate(value, i18n.language);
  const place = (centerId: string | null, purposeId: string | null) => {
    const center = centerId ? centerById.get(centerId) : undefined;
    return [center && name.city(center), purposeId && purposeById.get(purposeId)?.name]
      .filter(Boolean)
      .join(" · ");
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("queues.title")}</h1>
          <div className="muted">{t("queues.subtitle")}</div>
        </div>
      </header>

      {summary.isPending && <p className="muted">{t("common.loading")}</p>}
      {summary.data && summary.data.length === 0 && (
        <section className="card">
          <p className="muted">{t("queues.empty")}</p>
        </section>
      )}
      {summary.data && summary.data.length > 0 && (
        <>
          <div className="tabs" role="tablist" aria-label={t("queues.countries")}>
            {summary.data.map((row) => (
              <button
                key={row.country_id}
                type="button"
                role="tab"
                aria-selected={country === row.country_id}
                className={country === row.country_id ? "tab active" : "tab"}
                onClick={() => setPicked(row.country_id)}
              >
                {name.country(countryById.get(row.country_id))}
                <span className="tab-count">{row.waiting + row.dated + row.open_attempts}</span>
              </button>
            ))}
          </div>
          <label className="checkbox">
            <input type="checkbox" checked={closed} onChange={(e) => setClosed(e.target.checked)} />
            <span>{t("queues.showClosed")}</span>
          </label>
        </>
      )}

      {view.data && view.data.averages.length > 0 && (
        <section className="card">
          <h2 className="h2">{t("queues.averages")}</h2>
          <ul className="plain-list">
            {view.data.averages.map((a, i) => (
              <li key={i}>
                <span>{place(a.visa_center_id, a.purpose_id) || t("queues.anyPlace")}</span>
                <span>
                  {t("queues.averageDays", { count: a.average_days })}{" "}
                  <span className="muted">{t("queues.averageOf", { count: a.entries })}</span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {view.data && view.data.entries.length > 0 && (
        <section className="card">
          <h2 className="h2">{t("queues.waitingList")}</h2>
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("queues.applicant")}</th>
                  <th>{t("queues.number")}</th>
                  <th>{t("queues.email")}</th>
                  <th>{t("queues.place")}</th>
                  <th>{t("cases.registrar")}</th>
                  <th>{t("queues.queuedOn")}</th>
                  <th>{t("queues.letterDate")}</th>
                  <th>{t("queues.statusLabel")}</th>
                  <th>{t("queues.waits")}</th>
                </tr>
              </thead>
              <tbody>
                {view.data.entries.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link to={`/app/clients/${r.contact_id}`}>{r.full_name}</Link>
                      <div className="muted small">{t("cases.number", { number: r.number })}</div>
                    </td>
                    <td className="mono nowrap">{r.vac_number}</td>
                    <td>
                      {r.application_email}
                      {r.application_email_owner && (
                        <div className="muted small">{t(`queues.owner.${r.application_email_owner}`)}</div>
                      )}
                    </td>
                    <td>{place(r.visa_center_id, r.purpose_id)}</td>
                    <td>
                      {[
                        registrarById.get(r.registrar_id ?? "")?.name,
                        r.hall && t(`dict.hall.${r.hall}`),
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </td>
                    <td className="nowrap">{date(r.queued_on)}</td>
                    <td className="nowrap">
                      {date(r.letter_on)}
                      {r.submission_on && (
                        <div className="muted small">
                          {t("index.nextSubmission", { date: date(r.submission_on) })}
                        </div>
                      )}
                    </td>
                    <td>
                      <span className={QUEUE_CHIP[r.status]}>{t(`queues.status.${r.status}`)}</span>
                    </td>
                    <td className="nowrap">{t("queues.daysShort", { count: r.days_waiting })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {view.data && view.data.attempts.length > 0 && (
        <section className="card">
          <h2 className="h2">{t("queues.applications")}</h2>
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("queues.applicant")}</th>
                  <th>{t("queues.number")}</th>
                  <th>{t("queues.place")}</th>
                  <th>{t("queues.attemptNumber")}</th>
                  <th>{t("queues.sentOn")}</th>
                  <th>{t("queues.deadline")}</th>
                  <th>{t("queues.statusLabel")}</th>
                </tr>
              </thead>
              <tbody>
                {view.data.attempts.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <Link to={`/app/clients/${a.contact_id}`}>{a.full_name}</Link>
                      <div className="muted small">
                        {t("cases.number", { number: a.number_of_case })}
                      </div>
                    </td>
                    <td className="mono nowrap">{a.vac_number}</td>
                    <td>{place(a.visa_center_id, a.purpose_id)}</td>
                    <td>{a.number}</td>
                    <td className="nowrap">{date(a.sent_on)}</td>
                    <td className="nowrap">
                      {date(a.deadline_on)}
                      {a.days_left !== null && (
                        <div className="muted small">{t("queues.daysLeft", { count: a.days_left })}</div>
                      )}
                    </td>
                    <td>
                      <span className={ATTEMPT_CHIP[a.status]}>{t(`queues.attempt.${a.status}`)}</span>
                      {a.stage !== "waiting" && (
                        <div>
                          <CaseChip stage={a.stage} />
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {view.data && view.data.entries.length === 0 && view.data.attempts.length === 0 && (
        <section className="card">
          <p className="muted">{t("queues.nobodyHere")}</p>
        </section>
      )}
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
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
import { useDisclosure } from "../../components/Disclosure";
import { SortHeader } from "../../components/SortHeader";
import { byId, useCountries, useLocalName, useRules, type VisaCenter } from "../../reference";
import { useSortedTable, type SortColumns } from "../../sorting";
import { ATTEMPT_CHIP, QUEUE_CHIP } from "../clients/Appointment";
import { CaseChip } from "../clients/Cases";
import { ErrorAlert } from "../clients/ui";

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

type EntrySort = "applicant" | "status" | "place" | "registrar" | "queued" | "letter" | "waits";
type AttemptSort = "applicant" | "place" | "attempt" | "sent" | "deadline";

// A status sorts in the order a client goes through it, not by its name.
const STATUS_ORDER: Record<QueueRow["status"], number> = {
  in_queue: 0,
  date_received: 1,
  submitted: 2,
  removed: 3,
};
const NO_ENTRIES: QueueRow[] = [];
const NO_ATTEMPTS: AttemptRow[] = [];

export function QueuesPage() {
  const { t, i18n } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const rules = useRules();
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
  // The chosen country is in the URL (?country=<id>): the home screen links straight to
  // it, and back from a client's card the same tab opens.
  const [params, setParams] = useSearchParams();
  const picked = params.get("country") ?? "";
  // Another country keeps the chosen order of the tables.
  const setPicked = (id: string) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.set("country", id);
        return next;
      },
      { replace: true },
    );
  const [closed, setClosed] = useState(false);
  const averages = useDisclosure();
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
  const registrar = (r: QueueRow) =>
    [registrarById.get(r.registrar_id ?? "")?.name, r.hall && t(`dict.hall.${r.hall}`)]
      .filter(Boolean)
      .join(" · ");

  const entryColumns: SortColumns<QueueRow, EntrySort> = {
    applicant: { kind: "text", value: (r) => r.full_name },
    status: { kind: "number", value: (r) => STATUS_ORDER[r.status] },
    place: { kind: "text", value: (r) => place(r.visa_center_id, r.purpose_id) },
    registrar: { kind: "text", value: registrar },
    queued: { kind: "date", value: (r) => r.queued_on },
    letter: { kind: "date", value: (r) => r.letter_on },
    waits: { kind: "number", value: (r) => r.days_waiting, first: "desc" },
  };
  // The server's order: who has waited longest first.
  const entries = useSortedTable("sort", view.data?.entries ?? NO_ENTRIES, entryColumns, {
    key: "queued",
    dir: "asc",
  });
  const attemptColumns: SortColumns<AttemptRow, AttemptSort> = {
    applicant: { kind: "text", value: (a) => a.full_name },
    place: { kind: "text", value: (a) => place(a.visa_center_id, a.purpose_id) },
    attempt: { kind: "number", value: (a) => a.number, first: "desc" },
    sent: { kind: "date", value: (a) => a.sent_on },
    deadline: { kind: "date", value: (a) => a.deadline_on },
  };
  // The nearest 60th day first.
  const attempts = useSortedTable(
    "sort_attempts",
    view.data?.attempts ?? NO_ATTEMPTS,
    attemptColumns,
    { key: "deadline", dir: "asc" },
  );

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

      {country && view.isPending && <p className="muted">{t("common.loading")}</p>}
      {view.isError && <ErrorAlert error={view.error} />}

      {view.data && view.data.entries.length > 0 && (
        <section className="card">
          <h2 className="h2">{t("queues.waitingList")}</h2>
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <SortHeader {...entries.header("applicant")}>{t("queues.applicant")}</SortHeader>
                  <SortHeader {...entries.header("status")}>{t("queues.statusLabel")}</SortHeader>
                  <th>{t("queues.numberAndEmail")}</th>
                  <SortHeader {...entries.header("place")}>{t("queues.place")}</SortHeader>
                  <SortHeader {...entries.header("registrar")}>{t("cases.registrar")}</SortHeader>
                  <SortHeader {...entries.header("queued")}>{t("queues.queuedOn")}</SortHeader>
                  <SortHeader {...entries.header("letter")}>{t("queues.letterDate")}</SortHeader>
                  <SortHeader {...entries.header("waits")}>{t("queues.waits")}</SortHeader>
                </tr>
              </thead>
              <tbody>
                {entries.rows.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link to={`/app/clients/${r.contact_id}`}>{r.full_name}</Link>
                      <div className="muted small">{t("cases.number", { number: r.number })}</div>
                    </td>
                    <td>
                      <span className={QUEUE_CHIP[r.status]}>{t(`queues.status.${r.status}`)}</span>
                    </td>
                    <td>
                      {r.vac_number && <div className="mono nowrap">{r.vac_number}</div>}
                      {r.application_email && (
                        <div className="muted small">{r.application_email}</div>
                      )}
                      {r.application_email_owner && (
                        <div className="muted small">{t(`queues.owner.${r.application_email_owner}`)}</div>
                      )}
                    </td>
                    <td>{place(r.visa_center_id, r.purpose_id)}</td>
                    <td>{registrar(r)}</td>
                    <td className="nowrap">{date(r.queued_on)}</td>
                    <td className="nowrap">
                      {date(r.letter_on)}
                      {r.submission_on && (
                        <div className="muted small">
                          {t("index.nextSubmission", { date: date(r.submission_on) })}
                        </div>
                      )}
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
                  <SortHeader {...attempts.header("applicant")}>{t("queues.applicant")}</SortHeader>
                  <th>{t("queues.number")}</th>
                  <SortHeader {...attempts.header("place")}>{t("queues.place")}</SortHeader>
                  <SortHeader {...attempts.header("attempt")}>
                    {t("queues.attemptNumber")}
                  </SortHeader>
                  <SortHeader {...attempts.header("sent")}>{t("queues.sentOn")}</SortHeader>
                  <SortHeader {...attempts.header("deadline")}>{t("queues.deadline")}</SortHeader>
                  <th>{t("queues.statusLabel")}</th>
                </tr>
              </thead>
              <tbody>
                {attempts.rows.map((a) => (
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

      {/* Statistics, not the day's work: under the lists, open on demand. */}
      {view.data && view.data.averages.length > 0 && (
        <section className="card">
          <h2 className="h2">
            <button className="disclosure-title" {...averages.button}>
              {t("queues.averages")}
            </button>
          </h2>
          <ul className="plain-list" {...averages.panel}>
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
    </div>
  );
}

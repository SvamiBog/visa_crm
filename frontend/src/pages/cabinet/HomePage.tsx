import { useQuery } from "@tanstack/react-query";
import { useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiGet } from "../../api";
import { useMe } from "../../auth";
import {
  byId,
  useCountries,
  useLocalName,
  useOperators,
  useRules,
  type VisaCenter,
} from "../../reference";
import { useHasModule } from "../../workspace";
import { useMoney } from "../clients/Finance";
import { PackageChip } from "../clients/Package";
import { ErrorAlert } from "../clients/ui";
import { AttentionList } from "./Attention";

// Главный экран (backlog 1.12): what to do today — metrics, «Требует внимания», the
// nearest submissions, the month's funnel, the queues. The API leaves out the blocks
// whose modules the user does not have (app/api/home.py).

type Workspace = { name: string };

type Upcoming = {
  submission_on: string;
  case_id: string;
  case_number: number;
  contact_id: string;
  contact_name: string;
  others: number;
  destination_id: string;
  visa_center_id: string | null;
  stage: string;
  balance: string | null;
  // The documents of the line (package check); null: the stage is shown instead.
  package: { ready: boolean; missing: number } | null;
};

type Funnel = {
  month: string;
  consultations: number;
  decided: number;
  queued: number;
  submitted: number;
  visas: number;
};

type QueueCountry = {
  country_id: string;
  waiting: number;
  open_attempts: number;
  average_days: number | null;
  next_deadline: string | null;
};

type Home = {
  today: string;
  clients: {
    contacts: number;
    in_work: number;
    consultations: number;
    consultations_to_work: number;
    cases_opened: number;
  } | null;
  submissions: { week: number; next_on: string | null; upcoming: Upcoming[] } | null;
  funnel: Funnel | null;
  queues: {
    waiting: number;
    open_attempts: number;
    average_days: number | null;
    countries: QueueCountry[];
  } | null;
  money: {
    currency: string;
    debt: string;
    debtors: number;
    partners_due: string;
    partner_cases: number;
    registrars_due: { currency: string; amount: string; registrars: number }[];
  } | null;
};

const FUNNEL_STEPS = ["consultations", "decided", "queued", "submitted", "visas"] as const;

/** Dates of the API are days without a time: shown as they are, in any time zone. */
function useDay() {
  const { i18n } = useTranslation();
  return (value: string | null, options: Intl.DateTimeFormatOptions) => {
    if (!value) return "";
    const [y, m, d] = value.split("-").map(Number);
    return new Intl.DateTimeFormat(i18n.language, { ...options, timeZone: "UTC" }).format(
      new Date(Date.UTC(y, m - 1, d)),
    );
  };
}

const SHORT: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" };

/** The first day of the month `shift` months away from `month` (YYYY-MM-DD). */
function shiftMonth(month: string, shift: number): string {
  const [y, m] = month.split("-").map(Number);
  const moved = new Date(Date.UTC(y, m - 1 + shift, 1));
  return moved.toISOString().slice(0, 10);
}

function Kpi({
  to,
  label,
  value,
  note,
}: {
  to?: string;
  label: string;
  value: ReactNode;
  note?: ReactNode;
}) {
  const body = (
    <>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {note && <span className="kpi-note">{note}</span>}
    </>
  );
  return to ? (
    <Link className="kpi" to={to}>
      {body}
    </Link>
  ) : (
    <div className="kpi">{body}</div>
  );
}

/** Whole units: a tile shows the size of a sum, the kopecks are in the case. */
function useRounded() {
  const { i18n } = useTranslation();
  return (value: string) =>
    new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 0 }).format(Number(value));
}

function Metrics({ home }: { home: Home }) {
  const { t } = useTranslation();
  const money = useMoney();
  const rounded = useRounded();
  const day = useDay();
  // The registrars' page is the specialist's: a balance covers every case.
  const specialist = useMe().data?.role === "specialist";
  const { clients, submissions, queues, money: cash } = home;
  const share = (part: number, whole: number) => (whole ? Math.round((part / whole) * 100) : 0);
  return (
    <div className="kpis">
      {clients && (
        <>
          <Kpi
            to="/app/clients?tab=work"
            label={t("home.kpi.inWork")}
            value={clients.in_work}
            note={t("home.kpi.casesOpened", { count: clients.cases_opened })}
          />
          <Kpi
            to="/app/clients"
            label={t("home.kpi.consultations", {
              month: day(home.today, { month: "long" }),
            })}
            value={clients.consultations}
            note={t("home.kpi.toWork", {
              count: clients.consultations_to_work,
              percent: share(clients.consultations_to_work, clients.consultations),
            })}
          />
        </>
      )}
      {submissions && (
        <Kpi
          label={t("home.kpi.week")}
          value={submissions.week}
          note={
            submissions.next_on
              ? t("home.kpi.nextOn", { date: day(submissions.next_on, SHORT) })
              : t("home.kpi.noneAhead")
          }
        />
      )}
      {queues && (
        <Kpi
          to="/app/queues"
          label={t("home.kpi.queues")}
          value={queues.waiting + queues.open_attempts}
          note={
            queues.average_days !== null
              ? t("home.kpi.averageWait", { days: queues.average_days })
              : t("home.kpi.noWaits")
          }
        />
      )}
      {cash && (
        <>
          <Kpi
            label={t("home.kpi.debt")}
            value={
              <>
                {rounded(cash.debt)} <small>{cash.currency}</small>
              </>
            }
            note={t("home.kpi.debtors", { count: cash.debtors })}
          />
          <Kpi
            to={specialist ? "/app/registrars" : undefined}
            label={t("home.kpi.payable")}
            value={
              <>
                {rounded(cash.partners_due)} <small>{cash.currency}</small>
              </>
            }
            note={
              <>
                {t("home.kpi.partners", { count: cash.partner_cases })}
                {cash.registrars_due.map((r) => (
                  <span key={r.currency} className="kpi-line">
                    {t("home.kpi.registrars", {
                      amount: money(r.amount),
                      currency: r.currency,
                    })}
                  </span>
                ))}
              </>
            }
          />
        </>
      )}
    </div>
  );
}

function UpcomingSubmissions({
  rows,
  withMoney,
}: {
  rows: Upcoming[];
  withMoney: boolean;
}) {
  const { t } = useTranslation();
  const name = useLocalName();
  const money = useMoney();
  const day = useDay();
  const packageCheck = useHasModule("package_check");
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const rules = useRules();
  const operators = useOperators(false);
  const centers = useMemo(() => {
    const operatorById = byId(operators.data);
    const found = new Map<string, { center: VisaCenter; operator?: string }>();
    for (const rule of rules.data ?? [])
      for (const center of rule.visa_centers)
        found.set(center.id, {
          center,
          operator: operatorById.get(rule.vac_operator_id ?? "")?.name,
        });
    return found;
  }, [rules.data, operators.data]);
  const place = (id: string | null) => {
    const found = id ? centers.get(id) : undefined;
    return found ? [found.operator, name.city(found.center)].filter(Boolean).join(", ") : "—";
  };

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="h2">{t("home.upcoming")}</h2>
        <Link to="/app/clients?tab=work" className="small">
          {t("home.allClients")}
        </Link>
      </div>
      {rows.length === 0 ? (
        <p className="muted">{t("home.noUpcoming")}</p>
      ) : (
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>{t("home.col.date")}</th>
                <th>{t("home.col.client")}</th>
                <th>{t("home.col.country")}</th>
                <th>{t("home.col.center")}</th>
                <th>{t(packageCheck ? "home.col.documents" : "home.col.stage")}</th>
                {withMoney && <th className="money-cell">{t("home.col.balance")}</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.case_id}>
                  <td className="nowrap">{day(r.submission_on, { ...SHORT, weekday: "short" })}</td>
                  <td>
                    <Link to={`/app/clients/${r.contact_id}`}>{r.contact_name}</Link>
                    {r.others > 0 && (
                      <span className="muted"> {t("home.others", { count: r.others })}</span>
                    )}
                  </td>
                  <td>{name.country(countryById.get(r.destination_id))}</td>
                  <td>{place(r.visa_center_id)}</td>
                  <td>
                    {r.package ? (
                      <PackageChip ready={r.package.ready} missing={r.package.missing} />
                    ) : (
                      <span className="chip st-gray">{t(`cases.stage.${r.stage}`)}</span>
                    )}
                  </td>
                  {withMoney && (
                    <td className="money-cell">
                      {r.balance !== null && Number(r.balance) > 0 ? money(r.balance) : "—"}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** This month's funnel comes with the home screen; earlier months are fetched on demand. */
function MonthFunnel({ initial }: { initial: Funnel }) {
  const { t } = useTranslation();
  const day = useDay();
  const current = initial.month;
  const [month, setMonth] = useState(current);
  const funnel = useQuery({
    queryKey: ["home", "funnel", month],
    queryFn: () => apiGet<Funnel>(`/home/funnel?month=${month.slice(0, 7)}`),
    enabled: month !== current,
  });
  const data = month === current ? initial : funnel.data;
  const top = data?.consultations ?? 0;

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="h2">
          {t("home.funnel", { month: day(month, { month: "long", year: "numeric" }) })}
        </h2>
        <div className="month-switch">
          <button
            type="button"
            className="btn btn-sm"
            aria-label={t("home.prevMonth")}
            onClick={() => setMonth(shiftMonth(month, -1))}
          >
            ‹
          </button>
          <button
            type="button"
            className="btn btn-sm"
            aria-label={t("home.nextMonth")}
            disabled={month >= current}
            onClick={() => setMonth(shiftMonth(month, 1))}
          >
            ›
          </button>
        </div>
      </div>
      {funnel.isError && <ErrorAlert error={funnel.error} />}
      {data && (
        <ul className="funnel">
          {FUNNEL_STEPS.map((step, index) => (
            <li key={step} className="funnel-step">
              <div className="funnel-label">
                <span>{t(`home.step.${step}`)}</span>
                <span>
                  <strong>{data[step]}</strong>
                  {index > 0 && top > 0 && (
                    <span className="muted"> · {Math.round((data[step] / top) * 100)}%</span>
                  )}
                </span>
              </div>
              <div className="funnel-bar">
                <span style={{ width: top ? `${(data[step] / top) * 100}%` : 0 }} />
              </div>
            </li>
          ))}
        </ul>
      )}
      {data && top === 0 && <p className="muted small">{t("home.noConsultations")}</p>}
      <p className="muted small">{t("home.funnelHint")}</p>
    </section>
  );
}

function Queues({ rows }: { rows: QueueCountry[] }) {
  const { t } = useTranslation();
  const name = useLocalName();
  const day = useDay();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  return (
    <section className="card">
      <div className="card-head">
        <h2 className="h2">{t("home.queues")}</h2>
        <Link to="/app/queues" className="small">
          {t("home.allQueues")}
        </Link>
      </div>
      {rows.length === 0 ? (
        <p className="muted">{t("home.noQueues")}</p>
      ) : (
        <ul className="home-list">
          {rows.map((r) => (
            <li key={r.country_id}>
              <Link to={`/app/queues?country=${r.country_id}`}>
                {name.country(countryById.get(r.country_id))}
              </Link>
              <span className="muted small">
                {[
                  r.waiting > 0 && t("home.waiting", { count: r.waiting }),
                  r.open_attempts > 0 && t("home.attempts", { count: r.open_attempts }),
                  r.average_days !== null && t("home.average", { days: r.average_days }),
                  r.next_deadline &&
                    t("home.deadline", { date: day(r.next_deadline, SHORT) }),
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function HomePage() {
  const { t } = useTranslation();
  const tasks = useHasModule("tasks");
  const clients = useHasModule("clients");
  const day = useDay();
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<Workspace>("/workspace"),
  });
  const home = useQuery({ queryKey: ["home"], queryFn: () => apiGet<Home>("/home") });
  const data = home.data;

  return (
    <div className="page home-page">
      <header className="page-header">
        <div>
          <h1>{t("cabinet.homeTitle")}</h1>
          <div className="muted">
            {[
              workspace.data?.name,
              data && day(data.today, { weekday: "long", day: "numeric", month: "long" }),
            ]
              .filter(Boolean)
              .join(" · ")}
          </div>
        </div>
        {clients && (
          <Link to="/app/clients?new=1" className="btn btn-p">
            {t("home.newClient")}
          </Link>
        )}
      </header>

      {home.isPending && <p className="muted">{t("common.loading")}</p>}
      {home.isError && <ErrorAlert error={home.error} />}
      {data && <Metrics home={data} />}

      <div className="home-grid">
        <div className="column">
          {tasks && <AttentionList limit={8} />}
          {data?.submissions && (
            <UpcomingSubmissions rows={data.submissions.upcoming} withMoney={!!data.money} />
          )}
        </div>
        <div className="column">
          {data?.funnel && <MonthFunnel initial={data.funnel} />}
          {data?.queues && <Queues rows={data.queues.countries} />}
        </div>
      </div>
    </div>
  );
}

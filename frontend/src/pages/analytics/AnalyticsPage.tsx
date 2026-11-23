import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, apiGet, apiPut } from "../../api";
import { useMe } from "../../auth";
import { useCaseLists } from "../../contacts";
import {
  byId,
  useCountries,
  useLocalName,
  useOperators,
  useRules,
  type VisaCenter,
} from "../../reference";
import { SortHeader } from "../../components/SortHeader";
import { useSortedTable, type Sort, type SortColumns, type SortedTable } from "../../sorting";
import { useMoney } from "../clients/Finance";
import { Field, useFieldErrors } from "../../components/Field";
import { ErrorAlert } from "../clients/ui";

// Аналитика (backlog 1.13): reports of a period computed from the cards (app/api/analytics.py).
// Every report is a table; bars inside it only show the size, the numbers are always written.

type Source = { consultations: number; to_work: number; visas: number; profit: string | null };

type Report = {
  start: string;
  end: string;
  totals: {
    consultations: number;
    to_work: number;
    cases_opened: number;
    applicants: number;
    families: number;
    submissions: number;
    not_submitted: number;
    visas: number;
    refusals: number;
    approval: number | null;
    profit: string | null;
  };
  channels: (Source & { channel_id: string | null })[];
  partners: (Source & { partner_id: string })[];
  months: {
    month: string;
    consultations: number;
    to_work: number;
    submissions: number;
    visas: number;
    profit: string | null;
  }[];
  countries: {
    country_id: string;
    submissions: number;
    not_submitted: number;
    visas: number;
    refusals: number;
    approval: number | null;
    multiple: number;
    review_days: number | null;
  }[];
  results: Record<string, number>;
  centers: {
    visa_center_id: string;
    country_id: string;
    submissions: number;
    visas: number;
    refusals: number;
    approval: number | null;
    wait_days: number | null;
    attempts: number | null;
    review_days: number | null;
    note: string | null;
  }[];
  queues: {
    country_id: string;
    visa_center_id: string | null;
    purpose_id: string | null;
    waiting: number;
    letters: number;
    average_days: number | null;
    min_days: number | null;
    max_days: number | null;
    removed: number;
  }[];
  attempts: { country_id: string; dates: number; average_attempts: number | null }[];
  money: {
    currency: string;
    debt: string;
    debtors: number;
    partners_due: string;
    partner_cases: number;
    registrars_due: { currency: string; amount: string; registrars: number }[];
  } | null;
};

type Named = { id: string; name: string };

const RESULTS = [
  "single",
  "double",
  "multiple_short",
  "multiple_year",
  "multiple_long",
  "multiple",
  "visa",
  "visa_after_appeal",
  "refusal",
  "refusal_after_appeal",
  "not_submitted",
] as const;

const PRESETS = ["month", "quarter", "half", "year"] as const;
type Preset = (typeof PRESETS)[number];
const PRESET_MONTHS: Record<Preset, number> = { month: 1, quarter: 3, half: 6, year: 12 };

/** YYYY-MM of a month `shift` months from now. */
function monthOf(shift: number): string {
  const now = new Date();
  const d = new Date(now.getFullYear(), now.getMonth() + shift, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** First and last day of a range of months (YYYY-MM). */
function bounds(from: string, to: string): [string, string] {
  const [y, m] = to.split("-").map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return [`${from}-01`, `${to}-${String(last).padStart(2, "0")}`];
}

const share = (part: number, whole: number) => (whole ? Math.round((part / whole) * 100) : 0);

// Sortable reports: each table's column is in the URL (?sort_channels=-profit). Counts,
// percentages and money start from the largest, days from the shortest; the server's own
// order is marked where it is one of the columns. The months stay a timeline.
type SourceRow = Source & { label: string };
type SourceSort = "consultations" | "conversion" | "visas" | "profit";
const SOURCE_COLUMNS: SortColumns<SourceRow, SourceSort> = {
  consultations: { kind: "number", value: (r) => r.consultations, first: "desc" },
  conversion: {
    kind: "number",
    value: (r) => (r.consultations ? r.to_work / r.consultations : null),
    first: "desc",
  },
  visas: { kind: "number", value: (r) => r.visas, first: "desc" },
  profit: { kind: "money", value: (r) => r.profit, first: "desc" },
};
const BY_CONSULTATIONS: Sort<SourceSort> = { key: "consultations", dir: "desc" };

type CountryRow = Report["countries"][number] & { label: string };
type CountrySort =
  | "country"
  | "submissions"
  | "decisions"
  | "approval"
  | "multiple"
  | "notSubmitted"
  | "review";
const COUNTRY_COLUMNS: SortColumns<CountryRow, CountrySort> = {
  country: { kind: "text", value: (c) => c.label },
  submissions: { kind: "number", value: (c) => c.submissions, first: "desc" },
  decisions: { kind: "number", value: (c) => c.visas + c.refusals, first: "desc" },
  approval: { kind: "number", value: (c) => c.approval, first: "desc" },
  multiple: { kind: "number", value: (c) => c.multiple, first: "desc" },
  notSubmitted: { kind: "number", value: (c) => c.not_submitted, first: "desc" },
  review: { kind: "number", value: (c) => c.review_days },
};

type CenterRow = Report["centers"][number] & { label: string; country: string };
type CenterSort = "center" | "country" | "submissions" | "approval" | "wait" | "review";
const CENTER_COLUMNS: SortColumns<CenterRow, CenterSort> = {
  center: { kind: "text", value: (c) => c.label },
  country: { kind: "text", value: (c) => c.country },
  submissions: { kind: "number", value: (c) => c.submissions, first: "desc" },
  approval: { kind: "number", value: (c) => c.approval, first: "desc" },
  // Days of a waiting list; a center with application attempts (France) has none and
  // goes after the others.
  wait: { kind: "number", value: (c) => c.wait_days },
  review: { kind: "number", value: (c) => c.review_days },
};
const BY_SUBMISSIONS: Sort<CenterSort> = { key: "submissions", dir: "desc" };

type QueueRow = Report["queues"][number] & { label: string };
type QueueSort = "queue" | "waiting" | "average" | "removed";
const QUEUE_COLUMNS: SortColumns<QueueRow, QueueSort> = {
  queue: { kind: "text", value: (q) => q.label },
  waiting: { kind: "number", value: (q) => q.waiting, first: "desc" },
  average: { kind: "number", value: (q) => q.average_days, first: "desc" },
  removed: { kind: "number", value: (q) => q.removed, first: "desc" },
};
const BY_AVERAGE: Sort<QueueSort> = { key: "average", dir: "desc" };

/** A thin bar in a table cell: its length shows the size, the number stays in the cell. */
function Bar({ value, max, tone = "a", label }: { value: number; max: number; tone?: string; label: string }) {
  const width = max > 0 && value > 0 ? Math.max((value / max) * 100, 2) : 0;
  return (
    <span className="bar" title={label} aria-hidden="true">
      <span className={`bar-fill tone-${tone}`} style={{ width: `${width}%` }} />
    </span>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card">
      <h2 className="h2">{title}</h2>
      {hint && <p className="muted small analytics-hint">{hint}</p>}
      {children}
    </section>
  );
}

function Empty() {
  const { t } = useTranslation();
  return <p className="muted">{t("analytics.empty")}</p>;
}

function Legend() {
  const { t } = useTranslation();
  return (
    <div className="legend small">
      <span>
        <i className="swatch tone-a" /> {t("analytics.visa")}
      </span>
      <span>
        <i className="swatch tone-b" /> {t("analytics.refusal")}
      </span>
    </div>
  );
}

function CenterNote({ centerId, note }: { centerId: string; note: string | null }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(note ?? "");
  // The remarks are the specialist's own: an employee reads them (audit h07.1).
  const canEdit = useMe().data?.role === "specialist";
  const save = useMutation({
    mutationFn: () => apiPut<void>(`/analytics/centers/${centerId}/note`, { note: text }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["analytics"] });
      setEditing(false);
    },
  });
  if (!canEdit) return <span className="center-note">{note || "—"}</span>;
  if (!editing)
    return (
      <span className="center-note">
        <span>{note || "—"}</span>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => {
            setText(note ?? "");
            setEditing(true);
          }}
        >
          {note ? t("analytics.editNote") : t("analytics.addNote")}
        </button>
      </span>
    );
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  return (
    <form className="center-note" onSubmit={submit}>
      <input
        value={text}
        maxLength={500}
        aria-label={t("analytics.col.note")}
        onChange={(e) => setText(e.target.value)}
        autoFocus
      />
      <button type="submit" className="btn btn-sm btn-p" disabled={save.isPending}>
        {t("common.save")}
      </button>
      <button type="button" className="btn btn-sm" onClick={() => setEditing(false)}>
        {t("common.cancel")}
      </button>
      {save.isError && <ErrorAlert error={save.error} />}
    </form>
  );
}

export function AnalyticsPage() {
  const { t, i18n } = useTranslation();
  const name = useLocalName();
  const money = useMoney();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const rules = useRules();
  const operators = useOperators(false);
  const lists = useCaseLists();
  const purposeById = useMemo(() => byId(lists.purposes), [lists.purposes]);
  const channels = useQuery({
    queryKey: ["dictionaries", "channels"],
    queryFn: () => apiGet<Named[]>("/dictionaries/channels"),
  });
  const partners = useQuery({
    queryKey: ["dictionaries", "partners"],
    queryFn: () => apiGet<Named[]>("/dictionaries/partners"),
  });
  const channelById = useMemo(() => byId(channels.data), [channels.data]);
  const partnerById = useMemo(() => byId(partners.data), [partners.data]);
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

  const [preset, setPreset] = useState<Preset | "custom">("half");
  const [from, setFrom] = useState(monthOf(-5));
  const [to, setTo] = useState(monthOf(0));
  const [country, setCountry] = useState("");
  const pick = (p: Preset) => {
    setPreset(p);
    setFrom(monthOf(1 - PRESET_MONTHS[p]));
    setTo(monthOf(0));
  };
  const [start, end] = bounds(from <= to ? from : to, from <= to ? to : from);
  const report = useQuery({
    queryKey: ["analytics", start, end, country],
    queryFn: () =>
      apiGet<Report>(
        `/analytics?start=${start}&end=${end}${country ? `&country_id=${country}` : ""}`,
      ),
    placeholderData: keepPreviousData,
    // A period the API turns down (4xx) is not asked again: the reason shows at once.
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 3,
  });
  const data = report.data;
  // A period too long is the period's mistake: said under its end. A filter, not a form,
  // so the focus stays where it is.
  const errors = useFieldErrors(report.error, { codes: { invalid_period: "end" } });

  const sortedCountries = useMemo(
    () =>
      [...(countries.data ?? [])].sort((a, b) => name.country(a).localeCompare(name.country(b))),
    [countries.data, name],
  );
  const monthLabel = (value: string) => {
    const [y, m] = value.split("-").map(Number);
    return new Intl.DateTimeFormat(i18n.language, {
      month: "short",
      year: start.slice(0, 4) === end.slice(0, 4) ? undefined : "2-digit",
      timeZone: "UTC",
    }).format(new Date(Date.UTC(y, m - 1, 1)));
  };
  const place = (id: string | null) => {
    const found = id ? centers.get(id) : undefined;
    return found ? [found.operator, name.city(found.center)].filter(Boolean).join(" · ") : "";
  };
  const days = (value: number | null) => (value === null ? "—" : t("analytics.days", { days: value }));
  const percent = (value: number | null) => (value === null ? "—" : `${value}%`);
  const decimal = (value: number) =>
    new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 }).format(value);
  const withMoney = data?.money !== null && data?.money !== undefined;

  const channelTable = useSortedTable(
    "sort_channels",
    (data?.channels ?? []).map((r) => ({
      ...r,
      label: r.channel_id ? (channelById.get(r.channel_id)?.name ?? "—") : t("analytics.noChannel"),
    })),
    SOURCE_COLUMNS,
    BY_CONSULTATIONS,
  );
  const partnerTable = useSortedTable(
    "sort_partners",
    (data?.partners ?? []).map((r) => ({ ...r, label: partnerById.get(r.partner_id)?.name ?? "—" })),
    SOURCE_COLUMNS,
    BY_CONSULTATIONS,
  );
  const countryTable = useSortedTable(
    "sort_countries",
    (data?.countries ?? []).map((c) => ({ ...c, label: name.country(countryById.get(c.country_id)) })),
    COUNTRY_COLUMNS,
  );
  const centerTable = useSortedTable(
    "sort_centers",
    (data?.centers ?? []).map((c) => ({
      ...c,
      label: place(c.visa_center_id),
      country: name.country(countryById.get(c.country_id)),
    })),
    CENTER_COLUMNS,
    BY_SUBMISSIONS,
  );
  const queueTable = useSortedTable(
    "sort_queues",
    (data?.queues ?? []).map((q) => ({
      ...q,
      label: [
        name.country(countryById.get(q.country_id)),
        place(q.visa_center_id),
        q.purpose_id && purposeById.get(q.purpose_id)?.name,
      ]
        .filter(Boolean)
        .join(" · "),
    })),
    QUEUE_COLUMNS,
    BY_AVERAGE,
  );

  const sources = (table: SortedTable<SourceRow, SourceSort>) => {
    const top = Math.max(...table.rows.map((r) => r.consultations), 0);
    return (
      <div className="table-wrap">
        <table className="tbl analytics-table">
          <thead>
            <tr>
              <th />
              <SortHeader className="money-cell" {...table.header("consultations")}>
                {t("analytics.col.consultations")}
              </SortHeader>
              <SortHeader className="bar-col" {...table.header("conversion")}>
                {t("analytics.col.conversion")}
              </SortHeader>
              <SortHeader className="money-cell" {...table.header("visas")}>
                {t("analytics.col.visas")}
              </SortHeader>
              {withMoney && (
                <SortHeader className="money-cell" {...table.header("profit")}>
                  {t("analytics.col.profit")}
                </SortHeader>
              )}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r) => (
              <tr key={r.label}>
                <td>{r.label}</td>
                <td className="money-cell">
                  <span className="cell-bar">
                    <Bar value={r.consultations} max={top} label={`${r.consultations}`} />
                    <span className="cell-num">{r.consultations}</span>
                  </span>
                </td>
                <td className="bar-col">
                  {t("analytics.conversion", {
                    count: r.to_work,
                    percent: share(r.to_work, r.consultations),
                  })}
                </td>
                <td className="money-cell">{r.visas}</td>
                {withMoney && <td className="money-cell">{money(r.profit)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("analytics.title")}</h1>
          <div className="muted">{t("analytics.subtitle")}</div>
        </div>
      </header>

      <div className="card analytics-filters">
        <div className="tabs" role="tablist" aria-label={t("analytics.period")}>
          {PRESETS.map((p) => (
            <button
              key={p}
              type="button"
              role="tab"
              aria-selected={preset === p}
              className={preset === p ? "tab active" : "tab"}
              onClick={() => pick(p)}
            >
              {t(`analytics.preset.${p}`)}
            </button>
          ))}
        </div>
        <div className="filter-bar">
        <label className="field">
          <span>{t("analytics.from")}</span>
          <input
            type="month"
            value={from}
            onChange={(e) => {
              setPreset("custom");
              if (e.target.value) setFrom(e.target.value);
            }}
          />
        </label>
        <Field label={t("analytics.to")} error={errors.at("end")}>
          {(control) => (
            <input
              {...control}
              type="month"
              value={to}
              onChange={(e) => {
                setPreset("custom");
                if (e.target.value) setTo(e.target.value);
              }}
            />
          )}
        </Field>
        <label className="field">
          <span>{t("analytics.country")}</span>
          <select value={country} onChange={(e) => setCountry(e.target.value)}>
            <option value="">{t("analytics.allCountries")}</option>
            {sortedCountries.map((c) => (
              <option key={c.id} value={c.id}>
                {name.country(c)}
              </option>
            ))}
          </select>
        </label>
        </div>
      </div>

      {errors.general ? <ErrorAlert error={errors.general} /> : null}
      {report.isPending && <p className="muted">{t("common.loading")}</p>}
      {data && (
        <div
          className={report.isPlaceholderData ? "analytics is-stale" : "analytics"}
          aria-busy={report.isFetching}
        >
          <div className="kpis">
            <div className="kpi">
              <span className="kpi-label">{t("analytics.kpi.consultations")}</span>
              <span className="kpi-value">{data.totals.consultations}</span>
              <span className="kpi-note">
                {t("analytics.conversion", {
                  count: data.totals.to_work,
                  percent: share(data.totals.to_work, data.totals.consultations),
                })}
              </span>
            </div>
            <div className="kpi">
              <span className="kpi-label">{t("analytics.kpi.applicants")}</span>
              <span className="kpi-value">{data.totals.applicants}</span>
              <span className="kpi-note">
                {t("analytics.kpi.applicantsNote", {
                  cases: data.totals.cases_opened,
                  families: data.totals.families,
                })}
              </span>
            </div>
            <div className="kpi">
              <span className="kpi-label">{t("analytics.kpi.submissions")}</span>
              <span className="kpi-value">{data.totals.submissions}</span>
              <span className="kpi-note">
                {t("analytics.kpi.notSubmitted", { count: data.totals.not_submitted })}
              </span>
            </div>
            <div className="kpi">
              <span className="kpi-label">{t("analytics.kpi.approval")}</span>
              <span className="kpi-value">{percent(data.totals.approval)}</span>
              <span className="kpi-note">
                {t("analytics.kpi.decisions", {
                  visas: data.totals.visas,
                  refusals: data.totals.refusals,
                })}
              </span>
            </div>
            {data.money && (
              <div className="kpi">
                <span className="kpi-label">{t("analytics.kpi.profit")}</span>
                <span className="kpi-value">
                  {money(data.totals.profit)} <small>{data.money.currency}</small>
                </span>
                <span className="kpi-note">{t("analytics.kpi.profitNote")}</span>
              </div>
            )}
          </div>

          <Section title={t("analytics.channels")} hint={t("analytics.channelsHint")}>
            {data.channels.length === 0 ? <Empty /> : sources(channelTable)}
            {data.partners.length > 0 && (
              <>
                <h3 className="h3 analytics-sub">{t("analytics.partners")}</h3>
                {sources(partnerTable)}
              </>
            )}
          </Section>

          <Section title={t("analytics.months")} hint={t("analytics.monthsHint")}>
            <div className="table-wrap">
              <table className="tbl analytics-table">
                <thead>
                  <tr>
                    <th>{t("analytics.col.month")}</th>
                    <th className="money-cell">{t("analytics.col.consultations")}</th>
                    <th className="money-cell">{t("analytics.col.toWork")}</th>
                    <th className="money-cell">{t("analytics.col.submissions")}</th>
                    <th className="money-cell">{t("analytics.col.visas")}</th>
                    {withMoney && <th className="bar-col">{t("analytics.col.profit")}</th>}
                  </tr>
                </thead>
                <tbody>
                  {(() => {
                    const top = Math.max(...data.months.map((m) => Number(m.profit ?? 0)), 0);
                    return data.months.map((m) => (
                      <tr key={m.month}>
                        <td>{monthLabel(m.month)}</td>
                        <td className="money-cell">{m.consultations}</td>
                        <td className="money-cell">{m.to_work}</td>
                        <td className="money-cell">{m.submissions}</td>
                        <td className="money-cell">{m.visas}</td>
                        {withMoney && (
                          <td className="bar-col">
                            <span className="cell-bar">
                              <Bar
                                value={Number(m.profit ?? 0)}
                                max={top}
                                label={`${monthLabel(m.month)}: ${money(m.profit)}`}
                              />
                              {money(m.profit)}
                            </span>
                          </td>
                        )}
                      </tr>
                    ));
                  })()}
                </tbody>
              </table>
            </div>
          </Section>

          <Section title={t("analytics.countries")} hint={t("analytics.countriesHint")}>
            {data.countries.length === 0 ? (
              <Empty />
            ) : (
              <>
                <Legend />
                <div className="table-wrap">
                  <table className="tbl analytics-table">
                    <thead>
                      <tr>
                        <SortHeader {...countryTable.header("country")}>
                          {t("analytics.col.country")}
                        </SortHeader>
                        <SortHeader className="money-cell" {...countryTable.header("submissions")}>
                          {t("analytics.col.submissions")}
                        </SortHeader>
                        <SortHeader className="bar-col" {...countryTable.header("decisions")}>
                          {t("analytics.col.decisions")}
                        </SortHeader>
                        <SortHeader className="money-cell" {...countryTable.header("approval")}>
                          {t("analytics.col.approval")}
                        </SortHeader>
                        <SortHeader className="money-cell" {...countryTable.header("multiple")}>
                          {t("analytics.col.multiple")}
                        </SortHeader>
                        <SortHeader className="money-cell" {...countryTable.header("notSubmitted")}>
                          {t("analytics.col.notSubmitted")}
                        </SortHeader>
                        <SortHeader className="money-cell" {...countryTable.header("review")}>
                          {t("analytics.col.review")}
                        </SortHeader>
                      </tr>
                    </thead>
                    <tbody>
                      {(() => {
                        const top = Math.max(...data.countries.map((c) => c.visas + c.refusals), 0);
                        return countryTable.rows.map((c) => {
                          return (
                            <tr key={c.country_id}>
                              <td>{c.label}</td>
                              <td className="money-cell">{c.submissions}</td>
                              <td className="bar-col">
                                <span
                                  className="bar stacked"
                                  title={t("analytics.kpi.decisions", {
                                    visas: c.visas,
                                    refusals: c.refusals,
                                  })}
                                >
                                  {c.visas > 0 && (
                                    <span
                                      className="bar-fill tone-a"
                                      style={{ width: `${share(c.visas, top)}%` }}
                                    />
                                  )}
                                  {c.refusals > 0 && (
                                    <span
                                      className="bar-fill tone-b"
                                      style={{ width: `${share(c.refusals, top)}%` }}
                                    />
                                  )}
                                </span>
                                <span className="small">
                                  {c.visas} / {c.refusals}
                                </span>
                              </td>
                              <td className="money-cell">{percent(c.approval)}</td>
                              <td className="money-cell">{c.multiple}</td>
                              <td className="money-cell">{c.not_submitted}</td>
                              <td className="money-cell">{days(c.review_days)}</td>
                            </tr>
                          );
                        });
                      })()}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </Section>

          <Section title={t("analytics.results")} hint={t("analytics.resultsHint")}>
            {Object.keys(data.results).length === 0 ? (
              <Empty />
            ) : (
              <ul className="result-list">
                {(() => {
                  const top = Math.max(...Object.values(data.results), 0);
                  return RESULTS.filter((r) => data.results[r]).map((r) => {
                    const tone = r.startsWith("refusal") ? "b" : r === "not_submitted" ? "c" : "a";
                    return (
                      <li key={r}>
                        <span>{t(`analytics.result.${r}`)}</span>
                        <Bar value={data.results[r]} max={top} tone={tone} label={`${data.results[r]}`} />
                        <strong>{data.results[r]}</strong>
                      </li>
                    );
                  });
                })()}
              </ul>
            )}
          </Section>

          <Section title={t("analytics.centers")} hint={t("analytics.centersHint")}>
            {data.centers.length === 0 ? (
              <Empty />
            ) : (
              <div className="table-wrap">
                <table className="tbl analytics-table">
                  <thead>
                    <tr>
                      <SortHeader {...centerTable.header("center")}>
                        {t("analytics.col.center")}
                      </SortHeader>
                      <SortHeader {...centerTable.header("country")}>
                        {t("analytics.col.country")}
                      </SortHeader>
                      <SortHeader className="money-cell" {...centerTable.header("submissions")}>
                        {t("analytics.col.submissions")}
                      </SortHeader>
                      <SortHeader className="money-cell" {...centerTable.header("approval")}>
                        {t("analytics.col.approval")}
                      </SortHeader>
                      <SortHeader className="money-cell" {...centerTable.header("wait")}>
                        {t("analytics.col.wait")}
                      </SortHeader>
                      <SortHeader className="money-cell" {...centerTable.header("review")}>
                        {t("analytics.col.review")}
                      </SortHeader>
                      <th>{t("analytics.col.note")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {centerTable.rows.map((c) => (
                      <tr key={c.visa_center_id}>
                        <td>{c.label || "—"}</td>
                        <td>{c.country}</td>
                        <td className="money-cell">{c.submissions}</td>
                        <td className="money-cell">{percent(c.approval)}</td>
                        <td className="money-cell">
                          {c.wait_days !== null
                            ? days(c.wait_days)
                            : c.attempts !== null
                              ? t("analytics.attemptsShort", { value: decimal(c.attempts) })
                              : "—"}
                        </td>
                        <td className="money-cell">{days(c.review_days)}</td>
                        <td>
                          <CenterNote
                            key={c.note ?? ""}
                            centerId={c.visa_center_id}
                            note={c.note}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>

          <Section title={t("analytics.queues")} hint={t("analytics.queuesHint")}>
            {data.queues.length === 0 && data.attempts.length === 0 ? (
              <Empty />
            ) : (
              <>
                {data.queues.length > 0 && (
                  <div className="table-wrap">
                    <table className="tbl analytics-table">
                      <thead>
                        <tr>
                          <SortHeader {...queueTable.header("queue")}>
                            {t("analytics.col.queue")}
                          </SortHeader>
                          <SortHeader className="money-cell" {...queueTable.header("waiting")}>
                            {t("analytics.col.waitingNow")}
                          </SortHeader>
                          <SortHeader className="bar-col" {...queueTable.header("average")}>
                            {t("analytics.col.averageWait")}
                          </SortHeader>
                          <th className="money-cell">{t("analytics.col.spread")}</th>
                          <SortHeader className="money-cell" {...queueTable.header("removed")}>
                            {t("analytics.col.removed")}
                          </SortHeader>
                        </tr>
                      </thead>
                      <tbody>
                        {(() => {
                          const top = Math.max(...data.queues.map((q) => q.average_days ?? 0), 0);
                          return queueTable.rows.map((q) => {
                            const label = q.label;
                            return (
                              <tr key={`${q.country_id}-${q.visa_center_id}-${q.purpose_id}`}>
                                <td>{label}</td>
                                <td className="money-cell">{q.waiting}</td>
                                <td className="bar-col">
                                  {q.average_days !== null ? (
                                    <span className="cell-bar">
                                      <Bar
                                        value={q.average_days}
                                        max={top}
                                        label={`${label}: ${days(q.average_days)}`}
                                      />
                                      {t("analytics.averageOf", {
                                        days: q.average_days,
                                        count: q.letters,
                                      })}
                                    </span>
                                  ) : (
                                    "—"
                                  )}
                                </td>
                                <td className="money-cell">
                                  {q.min_days !== null && q.max_days !== null
                                    ? q.min_days === q.max_days
                                      ? q.min_days
                                      : `${q.min_days}–${q.max_days}`
                                    : "—"}
                                </td>
                                <td className="money-cell">{q.removed}</td>
                              </tr>
                            );
                          });
                        })()}
                      </tbody>
                    </table>
                  </div>
                )}
                {data.attempts.length > 0 && (
                  <>
                    <h3 className="h3 analytics-sub">{t("analytics.applications")}</h3>
                    <ul className="home-list">
                      {data.attempts.map((a) => (
                        <li key={a.country_id}>
                          <span>{name.country(countryById.get(a.country_id))}</span>
                          <span className="muted small">
                            {t("analytics.applicationsNote", {
                              dates: a.dates,
                              attempts:
                                a.average_attempts !== null ? decimal(a.average_attempts) : "—",
                            })}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </>
            )}
          </Section>

          {data.money && (
            <Section title={t("analytics.money")} hint={t("analytics.moneyHint")}>
              <ul className="home-list">
                <li>
                  <span>{t("analytics.debt")}</span>
                  <span>
                    <strong>
                      {money(data.money.debt)} {data.money.currency}
                    </strong>{" "}
                    <span className="muted small">
                      {t("home.kpi.debtors", { count: data.money.debtors })}
                    </span>
                  </span>
                </li>
                <li>
                  <span>{t("analytics.partnersDue")}</span>
                  <span>
                    <strong>
                      {money(data.money.partners_due)} {data.money.currency}
                    </strong>{" "}
                    <span className="muted small">
                      {t("home.kpi.partners", { count: data.money.partner_cases })}
                    </span>
                  </span>
                </li>
                {data.money.registrars_due.map((r) => (
                  <li key={r.currency}>
                    <span>{t("analytics.registrarsDue")}</span>
                    <strong>
                      {money(r.amount)} {r.currency}
                    </strong>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      )}
    </div>
  );
}

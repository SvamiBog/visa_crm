import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiGet, apiPost, apiPut } from "../../api";
import { formatDate } from "../../contacts";
import { byId, useCountries, useLocalName } from "../../reference";
import { useMoney } from "../clients/Finance";
import { ErrorAlert } from "../clients/ui";

// «Требует внимания» (backlog 1.11): reminders the API computes from the workspace's data
// (app/core/reminders.py). «Готово» hides one occurrence; the next one comes back.

type Kind =
  | "birthday"
  | "submission"
  | "balance_due"
  | "application_deadline"
  | "booking_deadline"
  | "thinking"
  | "visa_expires"
  | "appeal"
  | "registrar_advance"
  | "consultation"
  | "follow_up"
  | "contract"
  | "questionnaire"
  | "balance_queued"
  | "package"
  | "card_limit"
  | "password_limit";

type Reminder = {
  key: string | null;
  kind: Kind;
  occurs_on: string | null;
  due_on: string | null;
  days_left: number | null;
  since: string | null;
  contact_id: string | null;
  contact_name: string | null;
  case_id: string | null;
  case_number: number | null;
  destination_id: string | null;
  booking_kind: "hotel" | "flight" | null;
  registrar_id: string | null;
  registrar_name: string | null;
  amount: string | null;
  currency: string | null;
  employee_id: string | null;
  employee_name: string | null;
  limit: number | null;
  // Documents not collected (package): required ones still to collect.
  missing: number | null;
};

const remindersKey = (contactId?: string) => ["reminders", contactId ?? "all"] as const;

/** When the event is: overdue and today are urgent, the next days are close. */
function DaysChip({ days }: { days: number | null }) {
  const { t } = useTranslation();
  if (days === null) return null;
  const [cls, text] =
    days < 0
      ? ["chip st-red", t("attention.overdue", { days: -days })]
      : days === 0
        ? ["chip st-red", t("attention.today")]
        : days === 1
          ? ["chip st-orange", t("attention.tomorrow")]
          : [days <= 3 ? "chip st-orange" : "chip st-gray", t("attention.inDays", { days })];
  return <span className={cls}>{text}</span>;
}

function ReminderText({ item: r }: { item: Reminder }) {
  const { t, i18n } = useTranslation();
  const money = useMoney();
  const date = (value: string | null) => formatDate(value, i18n.language);
  switch (r.kind) {
    case "booking_deadline":
      return <>{t(`attention.kind.booking_${r.booking_kind}`, { date: date(r.due_on) })}</>;
    case "balance_due":
      return (
        <>
          {t("attention.kind.balance_due", {
            amount: money(r.amount),
            currency: r.currency,
            date: date(r.due_on),
          })}
        </>
      );
    case "thinking":
      return <>{t("attention.kind.thinking", { date: date(r.since) })}</>;
    case "follow_up":
      return (
        <>{t("attention.kind.follow_up", { date: date(r.due_on), since: date(r.since) })}</>
      );
    case "contract":
      return <>{t("attention.kind.contract", { date: date(r.since) })}</>;
    case "questionnaire":
      return (
        <>
          {t(
            (r.days_left ?? 0) < 0
              ? "attention.kind.questionnaire_overdue"
              : "attention.kind.questionnaire",
            { date: date(r.due_on) },
          )}
        </>
      );
    case "balance_queued":
      return (
        <>
          {t("attention.kind.balance_queued", {
            amount: money(r.amount),
            currency: r.currency,
            date: date(r.since),
          })}
        </>
      );
    case "package":
      return (
        <>{t("attention.kind.package", { date: date(r.due_on), count: r.missing ?? 0 })}</>
      );
    case "registrar_advance":
      return (
        <>
          {t("attention.kind.registrar_advance", {
            amount: money(r.amount),
            currency: r.currency,
          })}
        </>
      );
    case "card_limit":
    case "password_limit":
      // An employee went over a daily limit (backlog Т.14): paused until the next day.
      return (
        <>
          {t(`attention.kind.${r.kind}`, {
            name: r.employee_name,
            count: r.limit ?? 0,
            date: date(r.occurs_on),
          })}
        </>
      );
    case "application_deadline":
      return (
        <>
          {t(
            (r.days_left ?? 0) < 0
              ? "attention.kind.application_overdue"
              : "attention.kind.application_deadline",
            { date: date(r.due_on) },
          )}
        </>
      );
    default:
      return <>{t(`attention.kind.${r.kind}`, { date: date(r.due_on) })}</>;
  }
}

/** The reminders of the workspace, or of one client in their card (`contactId`). On the
 * home screen only the first `limit` show until «Показать все». */
export function AttentionList({ contactId, limit }: { contactId?: string; limit?: number }) {
  const { t } = useTranslation();
  const [all, setAll] = useState(false);
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: remindersKey(contactId),
    queryFn: () =>
      apiGet<Reminder[]>(contactId ? `/reminders?contact_id=${contactId}` : "/reminders"),
  });
  const done = useMutation({
    mutationFn: (key: string) => apiPost<void>("/reminders/dismiss", { key }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["reminders"] }),
  });

  // In a client's card the block shows only when there is something to do.
  if (contactId && (!list.data || list.data.length === 0)) return null;
  const shown = limit && !all ? list.data?.slice(0, limit) : list.data;
  const hidden = (list.data?.length ?? 0) - (shown?.length ?? 0);

  return (
    <section className="card attention">
      <h2 className="h2">{t("attention.title")}</h2>
      {list.isPending && <p className="muted">{t("common.loading")}</p>}
      {list.isError && <ErrorAlert error={list.error} />}
      {done.isError && <ErrorAlert error={done.error} />}
      {list.data?.length === 0 && <p className="muted">{t("attention.none")}</p>}
      {shown && shown.length > 0 && (
        <ul className="attention-list">
          {shown.map((r) => (
            <li key={r.key ?? `${r.kind}-${r.registrar_id}`} className="attention-item">
              <div className="attention-main">
                <div>
                  <ReminderText item={r} />
                </div>
                <div className="muted small">
                  {r.employee_id ? (
                    <Link to="/app/employees">{t("nav.employees")}</Link>
                  ) : r.registrar_id ? (
                    <Link to={`/app/registrars/${r.registrar_id}`}>{r.registrar_name}</Link>
                  ) : (
                    <>
                      {!contactId && r.contact_id && (
                        <Link to={`/app/clients/${r.contact_id}`}>{r.contact_name}</Link>
                      )}
                      {r.case_number !== null && (
                        <>
                          {!contactId && " · "}
                          {t("cases.number", { number: r.case_number })} ·{" "}
                          {name.country(countryById.get(r.destination_id ?? ""))}
                        </>
                      )}
                    </>
                  )}
                </div>
              </div>
              <div className="attention-side">
                {r.kind !== "thinking" && <DaysChip days={r.days_left} />}
                {r.key && (
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={done.isPending}
                    onClick={() => done.mutate(r.key!)}
                  >
                    {t("attention.done")}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {(hidden > 0 || all) && limit && (
        <button type="button" className="btn btn-sm" onClick={() => setAll(!all)}>
          {all ? t("attention.showLess") : t("attention.showAll", { count: list.data?.length })}
        </button>
      )}
    </section>
  );
}

type Settings = {
  submission_days: number;
  balance_days: number;
  application_days: number;
  booking_days: number;
  thinking_months: number[];
  visa_expiry_months: number;
  appeal_days: number;
  consultation_days: number;
  contract_days: number;
  questionnaire_days: number;
  package_days: number;
};

const NUMBER_FIELDS = [
  "submission_days",
  "balance_days",
  "application_days",
  "booking_days",
  "visa_expiry_months",
  "appeal_days",
  "consultation_days",
  "contract_days",
  "questionnaire_days",
  "package_days",
] as const;

/** The specialist's reminder windows («Справочники» → «Напоминания»). */
export function ReminderSettings() {
  const { t } = useTranslation();
  const settings = useQuery({
    queryKey: ["reminders", "settings"],
    queryFn: () => apiGet<Settings>("/reminders/settings"),
  });
  if (settings.isPending) return <p className="muted">{t("common.loading")}</p>;
  if (settings.isError) return <ErrorAlert error={settings.error} />;
  return <ReminderSettingsForm initial={settings.data} />;
}

function ReminderSettingsForm({ initial }: { initial: Settings }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(NUMBER_FIELDS.map((f) => [f, String(initial[f])])),
  );
  const [months, setMonths] = useState(initial.thinking_months.join(", "));
  const save = useMutation({
    mutationFn: () =>
      apiPut<Settings>("/reminders/settings", {
        ...Object.fromEntries(NUMBER_FIELDS.map((f) => [f, Number(values[f])])),
        thinking_months: months
          .split(/[\s,;]+/)
          .filter(Boolean)
          .map(Number),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["reminders", "settings"], data);
      void queryClient.invalidateQueries({ queryKey: ["reminders"] });
    },
  });
  return (
    <form
      className="card form-card"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <p className="muted small">{t("attention.settingsHint")}</p>
      <div className="form-grid">
        {NUMBER_FIELDS.map((field) => (
          <label key={field} className="field">
            <span>{t(`attention.setting.${field}`)}</span>
            <input
              type="number"
              min={1}
              max={field === "visa_expiry_months" ? 12 : 60}
              required
              value={values[field]}
              onChange={(e) => setValues((v) => ({ ...v, [field]: e.target.value }))}
            />
          </label>
        ))}
        <label className="field">
          <span>{t("attention.setting.thinking_months")}</span>
          <input
            required
            pattern="\s*\d{1,2}(\s*[,;\s]\s*\d{1,2}){0,2}\s*"
            value={months}
            onChange={(e) => setMonths(e.target.value)}
          />
        </label>
      </div>
      {save.isError && <ErrorAlert error={save.error} />}
      {save.isSuccess && (
        <div className="alert alert-ok" role="status">
          {t("attention.saved")}
        </div>
      )}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={save.isPending}>
          {t("common.save")}
        </button>
      </div>
    </form>
  );
}

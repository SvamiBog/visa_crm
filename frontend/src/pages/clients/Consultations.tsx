import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiGet, apiPatch, apiPost, apiPut, errorCode } from "../../api";
import { PURPOSE_KINDS } from "../../checklists";
import type { Disclosure } from "../../components/Disclosure";
import { Field, invalidProps, RowError, useFieldErrors } from "../../components/Field";
import {
  DECISIONS,
  VISA_KINDS,
  contactKey,
  formatDate,
  passportOf,
  refreshAfterPassportPurge,
  useContactLists,
  type Consultation,
  type ContactCard,
  type Decision,
  type Hint,
  type HistoryVisa,
  type Passport,
  type Questionnaire,
  type Refusal,
  type VisaRecord,
} from "../../contacts";
import { byId, useCountries, useLocalName } from "../../reference";
import { useMoney } from "./Finance";
import { useSortedCountries } from "./ui";

// Consultations, the questionnaire and its checks on the client's card (backlog 1.3).

// The consultation's fields that show the API's validation under them.
const CONSULTATION_FIELDS = [
  "held_on",
  "applicants",
  "follow_up_on",
  "trip_planned_on",
  "fee_amount",
  "fee_currency",
  "fee_paid_on",
  "notes",
] as const;

function ErrorAlert({ error }: { error: unknown }) {
  const { t } = useTranslation();
  return (
    <div className="alert alert-error" role="alert">
      {t(`errors.${errorCode(error)}`, { defaultValue: t("errors.unknown") })}
    </div>
  );
}

/** Today in the browser's calendar, as an ISO date for date inputs. */
export function todayIso(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

/** Pick several countries: a select to add one and chips to remove. `by` is the stored key. */
function CountryPicker({
  label,
  value,
  onChange,
  by,
}: {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  by: "id" | "code";
}) {
  const { t } = useTranslation();
  const name = useLocalName();
  const countries = useSortedCountries();
  const byKey = useMemo(() => new Map(countries.map((c) => [c[by], c])), [countries, by]);
  return (
    <div className="field">
      <span>{label}</span>
      {value.length > 0 && (
        <div className="chips">
          {value.map((key) => (
            <span key={key} className="chip st-gray chip-removable">
              {name.country(byKey.get(key)) || key}
              <button
                type="button"
                aria-label={t("contacts.removeCountry", { country: name.country(byKey.get(key)) })}
                onClick={() => onChange(value.filter((k) => k !== key))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <select
        aria-label={label}
        value=""
        onChange={(e) => e.target.value && onChange([...value, e.target.value])}
      >
        <option value="">{t("contacts.addCountry")}</option>
        {countries
          .filter((c) => !value.includes(c[by]))
          .map((c) => (
            <option key={c.id} value={c[by]}>
              {name.country(c)}
            </option>
          ))}
      </select>
    </div>
  );
}

// --- Hints ---------------------------------------------------------------------------

/** Questionnaire checks against the latest consultation; empty when nothing is wrong. */
export function Hints({ hints }: { hints: Hint[] }) {
  const { t } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const byCode = useMemo(
    () => new Map((countries.data ?? []).map((c) => [c.code, c])),
    [countries.data],
  );
  if (hints.length === 0) return null;
  const risky = new Set([
    "passport_expired",
    "passport_too_old",
    "passport_expires_too_soon",
    "refusal_before",
    "deportation",
  ]);
  return (
    <section className="card hints" aria-label={t("contacts.hintsTitle")}>
      <h2 className="h2">{t("contacts.hintsTitle")}</h2>
      <ul className="plain-list">
        {hints.map((hint, i) => (
          <li key={i}>
            <span>
              <span className={risky.has(hint.code) ? "chip st-orange" : "chip st-green"}>
                {t(risky.has(hint.code) ? "contacts.hintRisk" : "contacts.hintTip")}
              </span>{" "}
              {t(`contacts.hint.${hint.code}`, {
                country: hint.country ? name.country(byCode.get(hint.country)) || hint.country : "",
              })}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

// --- Consultations -------------------------------------------------------------------

const DECISION_CHIP: Record<Decision, string> = {
  yes: "chip st-green",
  no: "chip st-gray",
  thinking: "chip st-orange",
};

/** `onCreateCase` is given when the user may open cases («Создать кейс»). */
export function Consultations({
  card,
  onCreateCase,
}: {
  card: ContactCard;
  onCreateCase?: (consultation: Consultation) => void;
}) {
  const { t, i18n } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const money = useMoney();
  const [editing, setEditing] = useState<Consultation | "new" | null>(null);

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="h2">{t("contacts.consultations")}</h2>
        {editing === null && (
          <button type="button" className="btn btn-sm" onClick={() => setEditing("new")}>
            {t("contacts.addConsultation")}
          </button>
        )}
      </div>
      {editing !== null && (
        <ConsultationForm
          key={editing === "new" ? "new" : editing.id}
          card={card}
          consultation={editing === "new" ? undefined : editing}
          onDone={() => setEditing(null)}
        />
      )}
      {card.consultations.length === 0 && editing === null && (
        <p className="muted small">{t("contacts.noConsultations")}</p>
      )}
      <ul className="plain-list consultations">
        {card.consultations.map((c) => (
          <li key={c.id}>
            <div>
              <strong>{formatDate(c.held_on, i18n.language)}</strong>{" "}
              {c.decision ? (
                <span className={DECISION_CHIP[c.decision]}>{t(`contacts.decision.${c.decision}`)}</span>
              ) : (
                <span className="chip st-gray">{t("contacts.decision.none")}</span>
              )}
              <div className="muted small">
                {[
                  t("contacts.applicantsCount", { count: c.applicants }),
                  c.destination_ids.map((id) => name.country(countryById.get(id))).join(", "),
                  c.trip_planned_on &&
                    t("contacts.tripOn", { date: formatDate(c.trip_planned_on, i18n.language) }),
                  c.follow_up_on &&
                    t("contacts.followUpShort", {
                      date: formatDate(c.follow_up_on, i18n.language),
                    }),
                  c.fee_amount &&
                    `${t("contacts.fee")}: ${money(c.fee_amount)} ${c.fee_currency} · ${t(
                      c.fee_paid ? "contacts.feePaid" : "contacts.feeUnpaid",
                    )}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
              {c.notes && <p className="note-body">{c.notes}</p>}
              <OpenedCases consultation={c} contactId={card.contact.id} />
            </div>
            <div className="actions">
              {/* A consultation with a case already opened (maybe in the family's group) does
                  not offer another one here: «+ Кейс» in the cases block does (pilot fix 8). */}
              {onCreateCase && c.decision !== "no" && c.cases.length === 0 && (
                <button type="button" className="btn btn-sm" onClick={() => onCreateCase(c)}>
                  {t("cases.createFromHere")}
                </button>
              )}
              <button type="button" className="btn btn-sm" onClick={() => setEditing(c)}>
                {t("reference.edit")}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The cases a consultation led to, someone else's with a link to their card (pilot
 * fixes 2 and 8): «Открыт кейс №3 — NAUMIK MIKALAI, Польша · Ожидание записи». */
function OpenedCases({ consultation, contactId }: { consultation: Consultation; contactId: string }) {
  const { t } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  if (consultation.cases.length === 0) return null;
  return (
    <ul className="plain-list opened-cases">
      {consultation.cases.map((c) => (
        <li key={c.id} className="small">
          {t("cases.openedCase", { number: c.number })}
          {c.contact_id !== contactId && (
            <>
              {" — "}
              <Link to={`/app/clients/${c.contact_id}`}>{c.full_name}</Link>
            </>
          )}
          {", "}
          {[
            name.country(countryById.get(c.destination_id)),
            c.outcome ? t(`cases.outcome.${c.outcome}`) : t(`cases.stage.${c.stage}`),
          ]
            .filter(Boolean)
            .join(" · ")}
        </li>
      ))}
    </ul>
  );
}

/** A consultation as its form holds it: text for every input. */
export type ConsultationDraft = {
  held_on: string;
  applicants: string;
  decision: Decision | "";
  lead_channel_id: string;
  destination_ids: string[];
  trip_planned_on: string;
  follow_up_on: string;
  fee_amount: string;
  fee_currency: string;
  fee_paid: boolean;
  fee_paid_on: string;
  notes: string;
};

/** The form's start: the consultation's own values, or a new one of today for one
 * applicant with the card's channel. */
export function consultationDraft(consultation?: Consultation, channel = ""): ConsultationDraft {
  return {
    held_on: consultation?.held_on ?? todayIso(),
    applicants: String(consultation?.applicants ?? 1),
    decision: consultation?.decision ?? "",
    lead_channel_id: consultation ? (consultation.lead_channel_id ?? "") : channel,
    destination_ids: consultation?.destination_ids ?? [],
    trip_planned_on: consultation?.trip_planned_on ?? "",
    follow_up_on: consultation?.follow_up_on ?? "",
    fee_amount: consultation?.fee_amount ?? "",
    fee_currency: consultation?.fee_currency ?? "",
    fee_paid: consultation?.fee_paid ?? false,
    fee_paid_on: consultation?.fee_paid_on ?? "",
    notes: consultation?.notes ?? "",
  };
}

/** The body the API takes; the fee in the base currency unless another is picked. */
export function consultationBody(draft: ConsultationDraft, baseCurrency: string | undefined) {
  const amount = draft.fee_amount.trim().replace(",", ".");
  return {
    held_on: draft.held_on,
    applicants: Number(draft.applicants),
    decision: draft.decision || null,
    lead_channel_id: draft.lead_channel_id || null,
    destination_ids: draft.destination_ids,
    trip_planned_on: draft.trip_planned_on || null,
    follow_up_on: draft.follow_up_on || null,
    fee_amount: amount || null,
    fee_currency: amount ? draft.fee_currency || baseCurrency || null : null,
    fee_paid: amount ? draft.fee_paid : false,
    fee_paid_on: amount && draft.fee_paid ? draft.fee_paid_on || null : null,
    notes: draft.notes.trim() || null,
  };
}

/** The consultation's fields that show the API's validation under them, by their path in
 * the body ("consultation." in a new client's form). */
export function consultationFields(prefix = ""): string[] {
  return CONSULTATION_FIELDS.map((field) => prefix + field);
}

/** The base currency and the accepted ones (Lists → Currencies and tax). */
export function useMoneySettings() {
  return useQuery({
    queryKey: ["dictionaries", "money"],
    queryFn: () =>
      apiGet<{ base_currency: string; payment_currencies: string[] }>("/dictionaries/money"),
  });
}

function ConsultationForm({
  card,
  consultation,
  onDone,
}: {
  card: ContactCard;
  consultation?: Consultation;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const money = useMoneySettings();
  const [draft, setDraft] = useState(() =>
    consultationDraft(consultation, card.contact.lead_channel_id ?? ""),
  );

  const save = useMutation({
    mutationFn: () => {
      const body = consultationBody(draft, money.data?.base_currency);
      return consultation
        ? apiPatch<Consultation>(`/consultations/${consultation.id}`, body)
        : apiPost<Consultation>(`/contacts/${card.contact.id}/consultations`, body);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: contactKey(card.contact.id) });
      onDone();
    },
  });
  const errors = useFieldErrors(save.error, {
    codes: { currency_not_accepted: "fee_currency" },
    fields: CONSULTATION_FIELDS,
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form ref={errors.form} className="form-card consultation-form" onSubmit={submit}>
      <ConsultationInputs value={draft} onChange={setDraft} at={errors.at} />
      {errors.general ? <ErrorAlert error={errors.general} /> : null}
      <div className="form-actions">
        <button type="submit" className="btn btn-sm btn-p" disabled={save.isPending}>
          {t("common.save")}
        </button>
        <button type="button" className="btn btn-sm" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

/** The consultation's inputs, with no buttons of their own: the card's consultation form
 * and a new client's form (backlog 1.15.16) share them. On the card every field shows. A
 * new client's form passes `more`: the rarely needed part (the decision, when to call
 * back, the trip, the fee) opens under it, and the channel is left to the contact's
 * block. `at` gives a field's error by its path without the prefix. */
// In a new client's form the date and the number of applicants are not required: wiped
// out, they leave the block empty; in a block filled otherwise the API names them.
export function ConsultationInputs({
  value: draft,
  onChange,
  at,
  more,
}: {
  value: ConsultationDraft;
  onChange: (value: ConsultationDraft) => void;
  at: (field: string) => string | undefined;
  more?: Disclosure;
}) {
  const { t } = useTranslation();
  const lists = useContactLists();
  const money = useMoneySettings();
  const currencies = money.data?.payment_currencies ?? [];
  const set = <K extends keyof ConsultationDraft>(key: K, value: ConsultationDraft[K]) =>
    onChange({ ...draft, [key]: value });
  const feeMessage = useId();
  const feeError = at("fee_amount") ?? at("fee_currency") ?? at("fee_paid_on");

  const heldOn = (
    <Field label={t("contacts.heldOn")} error={at("held_on")}>
      {(control) => (
        <input
          {...control}
          type="date"
          required={!more}
          value={draft.held_on}
          onChange={(e) => set("held_on", e.target.value)}
        />
      )}
    </Field>
  );
  const applicants = (
    <Field label={t("contacts.applicants")} error={at("applicants")}>
      {(control) => (
        <input
          {...control}
          type="number"
          min={1}
          max={30}
          required={!more}
          value={draft.applicants}
          onChange={(e) => set("applicants", e.target.value)}
        />
      )}
    </Field>
  );
  const decision = (
    <label className="field">
      <span>{t("contacts.decisionLabel")}</span>
      <select
        value={draft.decision}
        onChange={(e) => set("decision", e.target.value as Decision | "")}
      >
        <option value="">{t("contacts.decision.none")}</option>
        {DECISIONS.map((d) => (
          <option key={d} value={d}>
            {t(`contacts.decision.${d}`)}
          </option>
        ))}
      </select>
    </label>
  );
  const followUp = (
    <Field
      label={t("contacts.followUpOn")}
      hint={t("contacts.followUpHint")}
      error={at("follow_up_on")}
    >
      {(control) => (
        <input
          {...control}
          type="date"
          value={draft.follow_up_on}
          onChange={(e) => set("follow_up_on", e.target.value)}
        />
      )}
    </Field>
  );
  const channel = (
    <label className="field">
      <span>{t("contacts.channel")}</span>
      <select
        value={draft.lead_channel_id}
        onChange={(e) => set("lead_channel_id", e.target.value)}
      >
        <option value="" />
        {lists.channels
          .filter((c) => c.is_active || c.id === draft.lead_channel_id)
          .map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
      </select>
    </label>
  );
  const trip = (
    <Field label={t("contacts.tripPlannedOn")} error={at("trip_planned_on")}>
      {(control) => (
        <input
          {...control}
          type="date"
          value={draft.trip_planned_on}
          onChange={(e) => set("trip_planned_on", e.target.value)}
        />
      )}
    </Field>
  );
  const destinations = (
    <CountryPicker
      label={t("contacts.destinations")}
      value={draft.destination_ids}
      onChange={(v) => set("destination_ids", v)}
      by="id"
    />
  );
  const fee = (
    <fieldset className="module-block">
      <legend>{t("contacts.fee")}</legend>
      <div className="row-editor">
        <input
          aria-label={t("dict.amount")}
          inputMode="decimal"
          placeholder={t("dict.amount")}
          value={draft.fee_amount}
          onChange={(e) => set("fee_amount", e.target.value)}
          {...invalidProps(feeMessage, at("fee_amount"))}
        />
        <select
          className="select-narrow"
          aria-label={t("dict.currency")}
          value={draft.fee_currency || money.data?.base_currency || ""}
          onChange={(e) => set("fee_currency", e.target.value)}
          {...invalidProps(feeMessage, at("fee_currency"))}
        >
          {currencies.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.fee_paid}
            disabled={!draft.fee_amount.trim()}
            onChange={(e) => set("fee_paid", e.target.checked)}
          />
          {t("contacts.feePaid")}
        </label>
        {draft.fee_paid && (
          <input
            type="date"
            aria-label={t("contacts.feePaidOn")}
            value={draft.fee_paid_on}
            onChange={(e) => set("fee_paid_on", e.target.value)}
            {...invalidProps(feeMessage, at("fee_paid_on"))}
          />
        )}
        <RowError id={feeMessage} error={feeError} />
      </div>
      <p className="muted small">{t("contacts.feeHint")}</p>
    </fieldset>
  );
  const notes = (
    <Field label={t("contacts.consultationNotes")} error={at("notes")}>
      {(control) => (
        <textarea
          {...control}
          rows={3}
          maxLength={5000}
          value={draft.notes}
          onChange={(e) => set("notes", e.target.value)}
        />
      )}
    </Field>
  );

  if (more) {
    return (
      <>
        <div className="form-grid">
          {heldOn}
          {applicants}
        </div>
        {destinations}
        {notes}
        <div>
          <button className="disclosure-title" {...more.button}>
            {t("newClient.consultationMore")}
          </button>
          <div className="form-card" {...more.panel}>
            <div className="form-grid">
              {decision}
              {followUp}
              {trip}
            </div>
            {fee}
          </div>
        </div>
      </>
    );
  }
  return (
    <>
      <div className="form-grid">
        {heldOn}
        {applicants}
        {decision}
        {followUp}
        {channel}
        {trip}
      </div>
      {destinations}
      {fee}
      {notes}
    </>
  );
}

// --- Questionnaire -------------------------------------------------------------------

/** One answer of the questionnaire that is a list (visas, refusals): a heading and a line
 * for each entry, or a single «—» row while there are none, so empty answers take no room. */
function AnswerList({
  label,
  empty,
  children,
}: {
  label: string;
  empty: boolean;
  children: ReactNode;
}) {
  if (empty) {
    return (
      <div className="kv">
        <span className="muted">{label}</span>
        <span>—</span>
      </div>
    );
  }
  return (
    <div className="q-section">
      <h3 className="h3">{label}</h3>
      <ul className="q-list">{children}</ul>
    </div>
  );
}

/** «Польша · Шенген · Туризм» with the term on the right and whether it is still valid.
 * A visa granted in the client's case names the case and leads to it (backlog A3). */
function VisaLine({
  visa: v,
  country,
  onOpenCase,
}: {
  visa: HistoryVisa;
  country: string;
  onOpenCase: (caseId: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const date = (value: string | null) => formatDate(value, i18n.language);
  // A case's country does not always name a kind (backend/app/core/visa_history.py).
  const kind = v.kind ? t(`contacts.visaKind.${v.kind}`) : "";
  // A visa with no country (the USA, say) is named by its kind alone.
  const title = country || kind;
  const period =
    v.valid_from && v.valid_to
      ? t("contacts.visaPeriod", { from: date(v.valid_from), to: date(v.valid_to) })
      : v.valid_to
        ? t("contacts.visaUntil", { date: date(v.valid_to) })
        : v.valid_from
          ? t("contacts.visaFrom", { date: date(v.valid_from) })
          : "";
  const { status, case_id: caseId } = v;
  return (
    <li className="q-item">
      <div className="q-main">
        <strong>{title}</strong>
        {country && kind && country !== kind && <span className="chip st-gray">{kind}</span>}
        {v.purpose && <span className="chip st-gray">{t(`purposeKind.${v.purpose}`)}</span>}
        {caseId !== null && (
          // A link to the block, opened by the page itself, as in the list of cases.
          <a
            className="q-case"
            href={`#case-${caseId}`}
            onClick={(e) => {
              e.preventDefault();
              onOpenCase(caseId);
            }}
          >
            {t("contacts.visaCase", { number: v.case_number })}
          </a>
        )}
      </div>
      {(period || status) && (
        <div className="q-meta">
          {period && <span>{period}</span>}
          {status && (
            <span className={status === "active" ? "chip st-green" : "chip st-gray"}>
              {t(status === "active" ? "contacts.visaActive" : "contacts.visaExpired")}
            </span>
          )}
        </div>
      )}
    </li>
  );
}

/** `onOpenCase`: brings a case of the card into view, for a visa granted in it. */
export function QuestionnaireCard({
  card,
  onOpenCase,
}: {
  card: ContactCard;
  onOpenCase: (caseId: string) => void;
}) {
  const { t } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const byCode = useMemo(
    () => new Map((countries.data ?? []).map((c) => [c.code, c])),
    [countries.data],
  );
  const [editing, setEditing] = useState(false);
  const q = card.questionnaire;
  const country = (code: string | null) => (code ? name.country(byCode.get(code)) || code : "");
  const names = (codes: string[]) => codes.map(country).join(", ");

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="h2">{t("contacts.questionnaire")}</h2>
        {!editing && (
          <div className="actions">
            <button type="button" className="btn btn-sm" onClick={() => setEditing(true)}>
              {t("reference.edit")}
            </button>
          </div>
        )}
      </div>
      {editing ? (
        <QuestionnaireForm card={card} onDone={() => setEditing(false)} />
      ) : (
        <>
          <AnswerList label={t("contacts.visas")} empty={card.visa_history.length === 0}>
            {card.visa_history.map((v, i) => (
              <VisaLine
                key={v.case_id ?? i}
                visa={v}
                country={country(v.country)}
                onOpenCase={onOpenCase}
              />
            ))}
          </AnswerList>
          <AnswerList label={t("contacts.refusals")} empty={q.refusals.length === 0}>
            {q.refusals.map((r, i) => (
              <li key={i} className="q-item">
                <div className="q-main">
                  <strong>{country(r.country)}</strong>
                  {r.year && <span className="chip st-gray">{r.year}</span>}
                </div>
                {r.reason && <div className="q-note">{r.reason}</div>}
              </li>
            ))}
          </AnswerList>
          <div className="kv">
            <span className="muted">{t("contacts.relativesAbroad")}</span>
            <span>{names(q.relatives_abroad) || "—"}</span>
          </div>
          {q.deportation ? (
            <p className="alert alert-error q-deportation">
              <strong>{t("contacts.deportationYes")}</strong>
              {q.deportation_note && <span>{q.deportation_note}</span>}
            </p>
          ) : (
            <div className="kv">
              <span className="muted">{t("contacts.deportation")}</span>
              <span>{t("contacts.no")}</span>
            </div>
          )}
          <div className="kv">
            <span className="muted">{t("contacts.humanitarianTrips")}</span>
            <span>{names(q.humanitarian_trips) || "—"}</span>
          </div>
          {q.note && <p className="note-body">{q.note}</p>}
        </>
      )}
    </section>
  );
}

function QuestionnaireForm({ card, onDone }: { card: ContactCard; onDone: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [q, setQ] = useState<Questionnaire>(card.questionnaire);

  const save = useMutation({
    mutationFn: () =>
      apiPut<Hint[]>(
        `/contacts/${card.contact.id}/questionnaire?${new URLSearchParams({
          // The passport goes as this card shows it; removed since, the API does not take
          // it back (backlog A11).
          card_updated_at: card.contact.updated_at,
        })}`,
        {
          ...q,
          // The passport is edited with the card: kept as the card has it now.
          ...passportOf(card.questionnaire),
          ...answersBody(q),
        },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: contactKey(card.contact.id) });
      onDone();
    },
    onError: (error) => {
      if (errorCode(error) === "passport_changed")
        void refreshAfterPassportPurge(queryClient, card.contact.id);
    },
  });
  const errors = useFieldErrors(save.error, { fields: answerFields(q) });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form ref={errors.form} className="form-card" onSubmit={submit}>
      <AnswersInputs value={q} onChange={setQ} at={errors.at} />
      {errors.general ? <ErrorAlert error={errors.general} /> : null}
      <div className="form-actions">
        <button type="submit" className="btn btn-sm btn-p" disabled={save.isPending}>
          {t("common.save")}
        </button>
        <button type="button" className="btn btn-sm" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

/** The questionnaire's answers, without the passport, as the API takes them. */
export type Answers = Omit<Questionnaire, keyof Passport>;

export const BLANK_ANSWERS: Answers = {
  visas: [],
  relatives_abroad: [],
  refusals: [],
  deportation: false,
  deportation_note: null,
  humanitarian_trips: [],
  note: null,
};

/** The answers' free text tidied as the API keeps it. */
export function answersBody<T extends Answers>(q: T): Answers {
  return {
    visas: q.visas,
    relatives_abroad: q.relatives_abroad,
    refusals: q.refusals,
    deportation: q.deportation,
    humanitarian_trips: q.humanitarian_trips,
    deportation_note: q.deportation ? q.deportation_note?.trim() || null : null,
    note: q.note?.trim() || null,
  };
}

/** The answers' fields that show the API's validation under them. Visas and refusals are
 * lists: the API names a row's field by its index. `prefix` is the answers' path in the
 * body ("questionnaire." in a new client's form). */
export function answerFields(q: Answers, prefix = ""): string[] {
  return [
    ...q.visas.flatMap((_, i) => [`visas.${i}.valid_from`, `visas.${i}.valid_to`]),
    ...q.refusals.flatMap((_, i) => [`refusals.${i}.year`, `refusals.${i}.reason`]),
    "deportation_note",
    "note",
  ].map((field) => prefix + field);
}

/** The questionnaire's answers as inputs, with no buttons of their own: the card's
 * questionnaire and a new client's form (backlog 1.15.16) share them. `at` gives a
 * field's error by its path without the prefix. */
export function AnswersInputs<T extends Answers>({
  value: q,
  onChange,
  at,
  paired = false,
}: {
  value: T;
  onChange: (value: T) => void;
  at: (field: string) => string | undefined;
  // Visas beside refusals and the two country pickers side by side on a wide screen
  // (a new client's form, where the answers take the whole width).
  paired?: boolean;
}) {
  const { t } = useTranslation();
  const countries = useSortedCountries();
  const pair = (children: ReactNode) =>
    paired ? <div className="form-pair">{children}</div> : children;
  const add = (button: ReactNode) =>
    paired ? <div className="form-actions">{button}</div> : button;
  const name = useLocalName();
  const set = <K extends keyof Answers>(key: K, value: Answers[K]) =>
    onChange({ ...q, [key]: value } as T);

  const countryOptions = (
    <>
      <option value="" />
      {countries.map((c) => (
        <option key={c.id} value={c.code}>
          {name.country(c)}
        </option>
      ))}
    </>
  );
  const setVisa = (i: number, patch: Partial<VisaRecord>) =>
    set("visas", q.visas.map((v, j) => (j === i ? { ...v, ...patch } : v)));
  const setRefusal = (i: number, patch: Partial<Refusal>) =>
    set("refusals", q.refusals.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const visas = (
    <fieldset className="module-block">
      <legend>{t("contacts.visas")}</legend>
      <div className="rows-editor">
        {q.visas.map((v, i) => (
          <div key={i} className="q-edit">
            <div className="form-grid">
              <label className="field">
                <span>{t("contacts.visaKindLabel")}</span>
                <select
                  value={v.kind}
                  onChange={(e) => setVisa(i, { kind: e.target.value as VisaRecord["kind"] })}
                >
                  {VISA_KINDS.map((k) => (
                    <option key={k} value={k}>
                      {t(`contacts.visaKind.${k}`)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>{t("dict.country")}</span>
                <select
                  value={v.country ?? ""}
                  onChange={(e) => setVisa(i, { country: e.target.value || null })}
                >
                  {countryOptions}
                </select>
              </label>
              <label className="field q-wide">
                <span>{t("contacts.visaPurpose")}</span>
                <select
                  value={v.purpose ?? ""}
                  onChange={(e) =>
                    setVisa(i, { purpose: (e.target.value || null) as VisaRecord["purpose"] })
                  }
                >
                  <option value="">{t("contacts.purposeNone")}</option>
                  {PURPOSE_KINDS.map((k) => (
                    <option key={k} value={k}>
                      {t(`purposeKind.${k}`)}
                    </option>
                  ))}
                </select>
              </label>
              <Field label={t("contacts.validFrom")} error={at(`visas.${i}.valid_from`)}>
                {(control) => (
                  <input
                    {...control}
                    type="date"
                    value={v.valid_from ?? ""}
                    onChange={(e) => setVisa(i, { valid_from: e.target.value || null })}
                  />
                )}
              </Field>
              <Field label={t("contacts.validTo")} error={at(`visas.${i}.valid_to`)}>
                {(control) => (
                  <input
                    {...control}
                    type="date"
                    min={v.valid_from ?? undefined}
                    value={v.valid_to ?? ""}
                    onChange={(e) => setVisa(i, { valid_to: e.target.value || null })}
                  />
                )}
              </Field>
            </div>
            <button
              type="button"
              className="link-button"
              onClick={() => set("visas", q.visas.filter((_, j) => j !== i))}
            >
              {t("dict.remove")}
            </button>
          </div>
        ))}
      </div>
      {add(
        <button
          type="button"
          className="btn btn-sm"
          onClick={() =>
            set("visas", [
              ...q.visas,
              {
                kind: "schengen",
                country: null,
                purpose: null,
                valid_from: null,
                valid_to: null,
                note: null,
              },
            ])
          }
        >
          {t("contacts.addVisa")}
        </button>
      )}
    </fieldset>
  );
  const refusals = (
    <fieldset className="module-block">
      <legend>{t("contacts.refusals")}</legend>
      <div className="rows-editor">
        {q.refusals.map((r, i) => (
          <div key={i} className="q-edit">
            <div className="form-grid">
              <label className="field">
                <span>{t("dict.country")}</span>
                <select
                  required
                  value={r.country}
                  onChange={(e) => setRefusal(i, { country: e.target.value })}
                >
                  {countryOptions}
                </select>
              </label>
              <Field label={t("contacts.year")} error={at(`refusals.${i}.year`)}>
                {(control) => (
                  <input
                    {...control}
                    type="number"
                    min={1990}
                    max={2100}
                    value={r.year ?? ""}
                    onChange={(e) =>
                      setRefusal(i, { year: e.target.value ? Number(e.target.value) : null })
                    }
                  />
                )}
              </Field>
              <Field
                className="field q-wide"
                label={t("contacts.reason")}
                error={at(`refusals.${i}.reason`)}
              >
                {(control) => (
                  <input
                    {...control}
                    maxLength={500}
                    value={r.reason ?? ""}
                    onChange={(e) => setRefusal(i, { reason: e.target.value || null })}
                  />
                )}
              </Field>
            </div>
            <button
              type="button"
              className="link-button"
              onClick={() => set("refusals", q.refusals.filter((_, j) => j !== i))}
            >
              {t("dict.remove")}
            </button>
          </div>
        ))}
      </div>
      {add(
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => set("refusals", [...q.refusals, { country: "", year: null, reason: null }])}
        >
          {t("contacts.addRefusal")}
        </button>
      )}
    </fieldset>
  );

  return (
    <>
      {pair(
        <>
          {visas}
          {refusals}
        </>,
      )}

      {pair(
        <>
          <CountryPicker
            label={t("contacts.relativesAbroad")}
            value={q.relatives_abroad}
            onChange={(v) => set("relatives_abroad", v)}
            by="code"
          />
          <CountryPicker
            label={t("contacts.humanitarianTrips")}
            value={q.humanitarian_trips}
            onChange={(v) => set("humanitarian_trips", v)}
            by="code"
          />
        </>,
      )}
      <label className="checkbox">
        <input
          type="checkbox"
          checked={q.deportation}
          onChange={(e) => set("deportation", e.target.checked)}
        />
        {t("contacts.deportation")}
      </label>
      {q.deportation && (
        <Field label={t("contacts.deportationNote")} error={at("deportation_note")}>
          {(control) => (
            <input
              {...control}
              maxLength={500}
              value={q.deportation_note ?? ""}
              onChange={(e) => set("deportation_note", e.target.value)}
            />
          )}
        </Field>
      )}
      <Field label={t("contacts.questionnaireNote")} error={at("note")}>
        {(control) => (
          <textarea
            {...control}
            rows={2}
            maxLength={2000}
            value={q.note ?? ""}
            onChange={(e) => set("note", e.target.value)}
          />
        )}
      </Field>
    </>
  );
}

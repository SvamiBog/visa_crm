import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { apiGet, apiPatch, apiPost, apiPut, errorCode } from "../../api";
import {
  DECISIONS,
  VISA_KINDS,
  contactKey,
  formatDate,
  passportOf,
  useContactLists,
  type Consultation,
  type ContactCard,
  type Decision,
  type Hint,
  type Questionnaire,
  type Refusal,
  type VisaRecord,
} from "../../contacts";
import { byId, useCountries, useLocalName } from "../../reference";
import { useSortedCountries } from "./ui";

// Consultations, the questionnaire and its checks on the client's card (backlog 1.3).

function ErrorAlert({ error }: { error: unknown }) {
  const { t } = useTranslation();
  return (
    <div className="alert alert-error" role="alert">
      {t(`errors.${errorCode(error)}`, { defaultValue: t("errors.unknown") })}
    </div>
  );
}

/** Today in the browser's calendar, as an ISO date for date inputs. */
function todayIso(): string {
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
  const [editing, setEditing] = useState<Consultation | "new" | null>(null);

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="h2">{t("contacts.consultations")}</h2>
        {editing === null && (
          <button type="button" className="btn btn-sm btn-p" onClick={() => setEditing("new")}>
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
                  c.fee_amount &&
                    `${t("contacts.fee")}: ${c.fee_amount} ${c.fee_currency} · ${t(
                      c.fee_paid ? "contacts.feePaid" : "contacts.feeUnpaid",
                    )}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
              {c.notes && <p className="note-body">{c.notes}</p>}
            </div>
            <div className="actions">
              {onCreateCase && c.decision !== "no" && (
                <button type="button" className="btn btn-sm btn-p" onClick={() => onCreateCase(c)}>
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
  const lists = useContactLists();
  const money = useQuery({
    queryKey: ["dictionaries", "money"],
    queryFn: () => apiGet<{ base_currency: string; payment_currencies: string[] }>("/dictionaries/money"),
  });
  const currencies = money.data?.payment_currencies ?? [];
  const [heldOn, setHeldOn] = useState(consultation?.held_on ?? todayIso());
  const [applicants, setApplicants] = useState(String(consultation?.applicants ?? 1));
  const [decision, setDecision] = useState<Decision | "">(consultation?.decision ?? "");
  const [channel, setChannel] = useState(
    consultation ? (consultation.lead_channel_id ?? "") : (card.contact.lead_channel_id ?? ""),
  );
  const [destinations, setDestinations] = useState<string[]>(consultation?.destination_ids ?? []);
  const [tripOn, setTripOn] = useState(consultation?.trip_planned_on ?? "");
  const [feeAmount, setFeeAmount] = useState(consultation?.fee_amount ?? "");
  const [feeCurrency, setFeeCurrency] = useState(consultation?.fee_currency ?? "");
  const [feePaid, setFeePaid] = useState(consultation?.fee_paid ?? false);
  const [feePaidOn, setFeePaidOn] = useState(consultation?.fee_paid_on ?? "");
  const [notes, setNotes] = useState(consultation?.notes ?? "");

  const save = useMutation({
    mutationFn: () => {
      const amount = feeAmount.trim().replace(",", ".");
      const body = {
        held_on: heldOn,
        applicants: Number(applicants),
        decision: decision || null,
        lead_channel_id: channel || null,
        destination_ids: destinations,
        trip_planned_on: tripOn || null,
        fee_amount: amount || null,
        fee_currency: amount ? feeCurrency || money.data?.base_currency || null : null,
        fee_paid: amount ? feePaid : false,
        fee_paid_on: amount && feePaid ? feePaidOn || null : null,
        notes: notes.trim() || null,
      };
      return consultation
        ? apiPatch<Consultation>(`/consultations/${consultation.id}`, body)
        : apiPost<Consultation>(`/contacts/${card.contact.id}/consultations`, body);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: contactKey(card.contact.id) });
      onDone();
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form className="form-card consultation-form" onSubmit={submit}>
      <div className="form-grid">
        <label className="field">
          <span>{t("contacts.heldOn")}</span>
          <input type="date" required value={heldOn} onChange={(e) => setHeldOn(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("contacts.applicants")}</span>
          <input
            type="number"
            min={1}
            max={30}
            required
            value={applicants}
            onChange={(e) => setApplicants(e.target.value)}
          />
        </label>
        <label className="field">
          <span>{t("contacts.decisionLabel")}</span>
          <select value={decision} onChange={(e) => setDecision(e.target.value as Decision | "")}>
            <option value="">{t("contacts.decision.none")}</option>
            {DECISIONS.map((d) => (
              <option key={d} value={d}>
                {t(`contacts.decision.${d}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("contacts.channel")}</span>
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="" />
            {lists.channels
              .filter((c) => c.is_active || c.id === channel)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
        </label>
        <label className="field">
          <span>{t("contacts.tripPlannedOn")}</span>
          <input type="date" value={tripOn} onChange={(e) => setTripOn(e.target.value)} />
        </label>
      </div>
      <CountryPicker
        label={t("contacts.destinations")}
        value={destinations}
        onChange={setDestinations}
        by="id"
      />
      <fieldset className="module-block">
        <legend>{t("contacts.fee")}</legend>
        <div className="row-editor">
          <input
            aria-label={t("dict.amount")}
            inputMode="decimal"
            placeholder={t("dict.amount")}
            value={feeAmount}
            onChange={(e) => setFeeAmount(e.target.value)}
          />
          <select
            aria-label={t("dict.currency")}
            value={feeCurrency || money.data?.base_currency || ""}
            onChange={(e) => setFeeCurrency(e.target.value)}
          >
            {currencies.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={feePaid}
              disabled={!feeAmount.trim()}
              onChange={(e) => setFeePaid(e.target.checked)}
            />
            {t("contacts.feePaid")}
          </label>
          {feePaid && (
            <input
              type="date"
              aria-label={t("contacts.feePaidOn")}
              value={feePaidOn}
              onChange={(e) => setFeePaidOn(e.target.value)}
            />
          )}
        </div>
        <p className="muted small">{t("contacts.feeHint")}</p>
      </fieldset>
      <label className="field">
        <span>{t("contacts.consultationNotes")}</span>
        <textarea rows={3} maxLength={5000} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </label>
      {save.isError && <ErrorAlert error={save.error} />}
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

// --- Questionnaire -------------------------------------------------------------------

export function QuestionnaireCard({ card }: { card: ContactCard }) {
  const { t, i18n } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const byCode = useMemo(
    () => new Map((countries.data ?? []).map((c) => [c.code, c])),
    [countries.data],
  );
  const [editing, setEditing] = useState(false);
  const q = card.questionnaire;
  const names = (codes: string[]) => codes.map((c) => name.country(byCode.get(c)) || c).join(", ");

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
          <div className="kv">
            <span className="muted">{t("contacts.visas")}</span>
            <span>
              {q.visas.length > 0
                ? q.visas
                    .map((v) =>
                      [
                        t(`contacts.visaKind.${v.kind}`),
                        v.country && name.country(byCode.get(v.country)),
                        v.valid_to && formatDate(v.valid_to, i18n.language),
                      ]
                        .filter(Boolean)
                        .join(" · "),
                    )
                    .join("; ")
                : "—"}
            </span>
          </div>
          <div className="kv">
            <span className="muted">{t("contacts.refusals")}</span>
            <span>
              {q.refusals.length > 0
                ? q.refusals
                    .map((r) =>
                      [name.country(byCode.get(r.country)) || r.country, r.year, r.reason]
                        .filter(Boolean)
                        .join(" · "),
                    )
                    .join("; ")
                : "—"}
            </span>
          </div>
          <div className="kv">
            <span className="muted">{t("contacts.relativesAbroad")}</span>
            <span>{names(q.relatives_abroad) || "—"}</span>
          </div>
          <div className="kv">
            <span className="muted">{t("contacts.deportation")}</span>
            <span>
              {q.deportation ? t("contacts.yesWith", { note: q.deportation_note ?? "" }) : t("contacts.no")}
            </span>
          </div>
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
  const countries = useSortedCountries();
  const name = useLocalName();
  const [q, setQ] = useState<Questionnaire>(card.questionnaire);
  const set = <K extends keyof Questionnaire>(key: K, value: Questionnaire[K]) =>
    setQ((current) => ({ ...current, [key]: value }));

  const save = useMutation({
    mutationFn: () =>
      apiPut<Hint[]>(`/contacts/${card.contact.id}/questionnaire`, {
        ...q,
        // The passport is edited with the card: kept as the card has it now.
        ...passportOf(card.questionnaire),
        deportation_note: q.deportation ? q.deportation_note?.trim() || null : null,
        note: q.note?.trim() || null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: contactKey(card.contact.id) });
      onDone();
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

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

  return (
    <form className="form-card" onSubmit={submit}>
      <fieldset className="module-block">
        <legend>{t("contacts.visas")}</legend>
        <div className="rows-editor">
          {q.visas.map((v, i) => (
            <div key={i} className="row-editor">
              <select
                aria-label={t("contacts.visaKindLabel")}
                value={v.kind}
                onChange={(e) => setVisa(i, { kind: e.target.value as VisaRecord["kind"] })}
              >
                {VISA_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {t(`contacts.visaKind.${k}`)}
                  </option>
                ))}
              </select>
              <select
                aria-label={t("dict.country")}
                value={v.country ?? ""}
                onChange={(e) => setVisa(i, { country: e.target.value || null })}
              >
                {countryOptions}
              </select>
              <input
                type="date"
                aria-label={t("contacts.validFrom")}
                value={v.valid_from ?? ""}
                onChange={(e) => setVisa(i, { valid_from: e.target.value || null })}
              />
              <input
                type="date"
                aria-label={t("contacts.validTo")}
                value={v.valid_to ?? ""}
                onChange={(e) => setVisa(i, { valid_to: e.target.value || null })}
              />
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => set("visas", q.visas.filter((_, j) => j !== i))}
              >
                {t("dict.remove")}
              </button>
            </div>
          ))}
        </div>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() =>
            set("visas", [
              ...q.visas,
              { kind: "schengen", country: null, valid_from: null, valid_to: null, note: null },
            ])
          }
        >
          {t("contacts.addVisa")}
        </button>
      </fieldset>

      <fieldset className="module-block">
        <legend>{t("contacts.refusals")}</legend>
        <div className="rows-editor">
          {q.refusals.map((r, i) => (
            <div key={i} className="row-editor">
              <select
                aria-label={t("dict.country")}
                required
                value={r.country}
                onChange={(e) => setRefusal(i, { country: e.target.value })}
              >
                {countryOptions}
              </select>
              <input
                type="number"
                min={1990}
                max={2100}
                aria-label={t("contacts.year")}
                placeholder={t("contacts.year")}
                value={r.year ?? ""}
                onChange={(e) => setRefusal(i, { year: e.target.value ? Number(e.target.value) : null })}
              />
              <input
                aria-label={t("contacts.reason")}
                placeholder={t("contacts.reason")}
                maxLength={500}
                value={r.reason ?? ""}
                onChange={(e) => setRefusal(i, { reason: e.target.value || null })}
              />
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => set("refusals", q.refusals.filter((_, j) => j !== i))}
              >
                {t("dict.remove")}
              </button>
            </div>
          ))}
        </div>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => set("refusals", [...q.refusals, { country: "", year: null, reason: null }])}
        >
          {t("contacts.addRefusal")}
        </button>
      </fieldset>

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
      <label className="checkbox">
        <input
          type="checkbox"
          checked={q.deportation}
          onChange={(e) => set("deportation", e.target.checked)}
        />
        {t("contacts.deportation")}
      </label>
      {q.deportation && (
        <label className="field">
          <span>{t("contacts.deportationNote")}</span>
          <input
            maxLength={500}
            value={q.deportation_note ?? ""}
            onChange={(e) => set("deportation_note", e.target.value)}
          />
        </label>
      )}
      <label className="field">
        <span>{t("contacts.questionnaireNote")}</span>
        <textarea rows={2} maxLength={2000} value={q.note ?? ""} onChange={(e) => set("note", e.target.value)} />
      </label>
      {save.isError && <ErrorAlert error={save.error} />}
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

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiDelete, apiGet, apiPatch, apiPost, errorCode } from "../../api";
import {
  CASE_STAGES,
  EMAIL_OWNERS,
  HALLS,
  OUTCOMES,
  VISA_ENTRIES,
  formatDate,
  useCaseLists,
  type CaseStage,
  type Consultation,
  type ContactCard,
  type EmailOwner,
  type GroupMember,
  type Hall,
  type Outcome,
  type VisaCase,
  type VisaEntries,
} from "../../contacts";
import {
  DEFAULT_SUBMISSION_CODE,
  byId,
  useCountries,
  useLocalName,
  useRules,
  type Country,
} from "../../reference";
import { Booking, useNumberLabel } from "./Booking";

// Cases on the client's card (backlog 1.4): one visa for one applicant, the stage,
// the structured visa result and the submission group.

function ErrorAlert({ error }: { error: unknown }) {
  const { t } = useTranslation();
  return (
    <div className="alert alert-error" role="alert">
      {t(`errors.${errorCode(error)}`, { defaultValue: t("errors.unknown") })}
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="kv">
      <span className="muted">{label}</span>
      <span>{children || "—"}</span>
    </div>
  );
}

function useSortedCountries(): Country[] {
  const name = useLocalName();
  const countries = useCountries();
  return useMemo(
    () =>
      [...(countries.data ?? [])].sort((a, b) => name.country(a).localeCompare(name.country(b))),
    [countries.data, name],
  );
}

/** Every card that shows a case: the applicants' and their family's. */
function useRefreshCards() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["contacts"] });
}

const OUTCOME_CHIP: Record<Outcome, string> = {
  visa: "chip st-green",
  refusal: "chip st-red",
  not_submitted: "chip st-gray",
};

/** The case's chip: its stage, or the result once there is one. */
export function CaseChip({ stage, outcome }: { stage: CaseStage; outcome?: Outcome | null }) {
  const { t } = useTranslation();
  if (outcome) return <span className={OUTCOME_CHIP[outcome]}>{t(`cases.outcome.${outcome}`)}</span>;
  return (
    <span className={stage === "waiting" ? "chip st-orange" : "chip st-blue"}>
      {t(`cases.stage.${stage}`)}
    </span>
  );
}

// --- The cases block -----------------------------------------------------------------

/** `creating`: a consultation to open cases from («Создать кейс»), "blank" for a case
 * without one, null when no form is open. */
export function Cases({
  card,
  creating,
  onCreating,
}: {
  card: ContactCard;
  creating: Consultation | "blank" | null;
  onCreating: (value: Consultation | "blank" | null) => void;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLElement>(null);
  const cases = card.cases ?? [];

  useEffect(() => {
    if (creating !== null) ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [creating]);

  return (
    <section className="card" ref={ref}>
      <div className="card-head">
        <h2 className="h2">{t("cases.title")}</h2>
        {creating === null && (
          <button type="button" className="btn btn-sm btn-p" onClick={() => onCreating("blank")}>
            {t("cases.add")}
          </button>
        )}
      </div>
      {creating !== null && (
        <CaseCreateForm
          key={creating === "blank" ? "blank" : creating.id}
          card={card}
          consultation={creating === "blank" ? undefined : creating}
          onDone={() => onCreating(null)}
        />
      )}
      {cases.length === 0 && creating === null && (
        <p className="muted small">{t("cases.empty")}</p>
      )}
      <div className="cases">
        {cases.map((c) => (
          <CasePanel key={c.id} visaCase={c} />
        ))}
      </div>
    </section>
  );
}

function CasePanel({ visaCase: c }: { visaCase: VisaCase }) {
  const { t, i18n } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const lists = useCaseLists();
  const rules = useRules(false, c.submission_country_id);
  const refresh = useRefreshCards();
  const [editing, setEditing] = useState(false);
  const moveTo = useMutation({
    mutationFn: (stage: CaseStage) => apiPatch<VisaCase>(`/cases/${c.id}`, { stage }),
    onSuccess: () => void refresh(),
  });

  const center = rules.data
    ?.find((r) => r.destination_id === c.destination_id)
    ?.visa_centers.find((v) => v.id === c.visa_center_id);
  const purpose = lists.purposes.find((p) => p.id === c.purpose_id);
  const registrar = lists.registrars.find((r) => r.id === c.registrar_id);
  const date = (value: string | null) => formatDate(value, i18n.language);
  const current = CASE_STAGES.indexOf(c.stage);

  return (
    <article className="case-panel">
      <div className="card-head">
        <h3 className="h3">
          {t("cases.number", { number: c.number })} · {name.country(countryById.get(c.destination_id))}
        </h3>
        <CaseChip stage={c.stage} outcome={c.outcome} />
      </div>

      <ol className="stepper" aria-label={t("cases.stageLabel")}>
        {CASE_STAGES.map((stage, i) => (
          <li key={stage} className={i < current ? "done" : i === current ? "current" : undefined}>
            <button
              type="button"
              aria-current={i === current ? "step" : undefined}
              disabled={i === current || moveTo.isPending}
              onClick={() => moveTo.mutate(stage)}
            >
              {t(`cases.stage.${stage}`)}
            </button>
          </li>
        ))}
      </ol>
      {moveTo.isError && <ErrorAlert error={moveTo.error} />}

      {editing ? (
        <CaseEditForm visaCase={c} onDone={() => setEditing(false)} />
      ) : (
        <>
          <Row label={t("cases.purpose")}>{purpose?.name}</Row>
          <Row label={t("cases.where")}>
            {[center && name.city(center), name.country(countryById.get(c.submission_country_id))]
              .filter(Boolean)
              .join(", ")}
          </Row>
          <Row label={t("cases.registrar")}>
            {[registrar?.name, c.hall && t(`dict.hall.${c.hall}`)].filter(Boolean).join(" · ")}
          </Row>
          <Row label={t("cases.submissionOn")}>{date(c.submission_on)}</Row>
          <Booking visaCase={c} />
          {c.outcome && <Result visaCase={c} />}
          <Group visaCase={c} />
          <div className="form-actions">
            <button type="button" className="btn btn-sm" onClick={() => setEditing(true)}>
              {t("reference.edit")}
            </button>
          </div>
        </>
      )}
    </article>
  );
}

function Result({ visaCase: c }: { visaCase: VisaCase }) {
  const { t, i18n } = useTranslation();
  const date = (value: string | null) => formatDate(value, i18n.language);
  const validity =
    c.visa_valid_from || c.visa_valid_to
      ? `${date(c.visa_valid_from) || "…"} – ${date(c.visa_valid_to) || "…"}`
      : null;
  return (
    <>
      <Row label={t("cases.result")}>
        {[
          t(`cases.outcome.${c.outcome}`),
          c.visa_entries && t(`cases.entries.${c.visa_entries}`),
          validity,
          c.stay_days && t("cases.stayDaysValue", { count: c.stay_days }),
          c.after_appeal && t("cases.afterAppeal"),
        ]
          .filter(Boolean)
          .join(" · ")}
      </Row>
      <Row label={t("cases.decidedOn")}>{date(c.decided_on)}</Row>
      {c.outcome === "refusal" && !c.after_appeal && (
        <Row label={t("cases.appeal")}>
          {c.appeals_accepted === false
            ? t("cases.appealNotAccepted")
            : c.appeal_until
              ? t("cases.appealUntil", { date: date(c.appeal_until) })
              : c.appeals_accepted
                ? t("cases.appealDeadlineUnknown")
                : t("cases.appealUnknown")}
        </Row>
      )}
    </>
  );
}

function Group({ visaCase: c }: { visaCase: VisaCase }) {
  const { t } = useTranslation();
  const refresh = useRefreshCards();
  const [adding, setAdding] = useState(false);
  const [picked, setPicked] = useState("");
  const candidates = useQuery({
    queryKey: ["cases", c.id, "group-candidates"],
    queryFn: () => apiGet<GroupMember[]>(`/cases/${c.id}/group-candidates`),
    enabled: adding,
  });
  const join = useMutation({
    mutationFn: () => apiPost<VisaCase>(`/cases/${c.id}/group`, { case_id: picked }),
    onSuccess: () => {
      setAdding(false);
      setPicked("");
      void refresh();
    },
  });
  const leave = useMutation({
    mutationFn: () => apiDelete<void>(`/cases/${c.id}/group`),
    onSuccess: () => void refresh(),
  });

  return (
    <div className="case-group">
      <Row label={t("cases.group")}>
        {c.group.length > 0 &&
          c.group.map((m, i) => (
            <span key={m.case_id}>
              {i > 0 && ", "}
              <Link to={`/app/clients/${m.contact_id}`}>{m.full_name}</Link>
              <span className="muted"> {t("cases.number", { number: m.number })}</span>
            </span>
          ))}
      </Row>
      {adding ? (
        <form
          className="form-card"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            join.mutate();
          }}
        >
          {candidates.data && candidates.data.length === 0 ? (
            <p className="muted small">{t("cases.noCandidates")}</p>
          ) : (
            <label className="field">
              <span>{t("cases.joinWith")}</span>
              <select required value={picked} onChange={(e) => setPicked(e.target.value)}>
                <option value="" />
                {(candidates.data ?? []).map((m) => (
                  <option key={m.case_id} value={m.case_id}>
                    {m.full_name} · {t("cases.number", { number: m.number })} ·{" "}
                    {t(`cases.stage.${m.stage}`)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {join.isError && <ErrorAlert error={join.error} />}
          <div className="form-actions">
            <button type="submit" className="btn btn-sm btn-p" disabled={!picked || join.isPending}>
              {t("cases.join")}
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setAdding(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      ) : (
        <div className="note-actions">
          <button type="button" className="link-button" onClick={() => setAdding(true)}>
            {t("cases.addToGroup")}
          </button>
          {c.group_id && (
            <button
              type="button"
              className="link-button"
              disabled={leave.isPending}
              onClick={() => {
                if (window.confirm(t("cases.confirmLeave"))) leave.mutate();
              }}
            >
              {t("cases.leaveGroup")}
            </button>
          )}
        </div>
      )}
      {leave.isError && <ErrorAlert error={leave.error} />}
    </div>
  );
}

// --- Forms ---------------------------------------------------------------------------

type Place = {
  destination: string;
  submission: string;
  center: string;
  purpose: string;
  registrar: string;
  hall: Hall | "";
  submissionOn: string;
};

function placeBody(place: Place) {
  return {
    destination_id: place.destination,
    submission_country_id: place.submission,
    visa_center_id: place.center || null,
    purpose_id: place.purpose || null,
    registrar_id: place.registrar || null,
    hall: place.hall || null,
    submission_on: place.submissionOn || null,
  };
}

/** Country, where it is submitted (country and city), purpose, registrar and hall. */
function PlaceFields({
  place,
  onChange,
  suggested = [],
}: {
  place: Place;
  onChange: (place: Place) => void;
  suggested?: string[];
}) {
  const { t } = useTranslation();
  const name = useLocalName();
  const countries = useSortedCountries();
  const lists = useCaseLists();
  const rules = useRules(false, place.submission || undefined);
  const rule = rules.data?.find((r) => r.destination_id === place.destination);
  const centers = rule?.visa_centers ?? [];
  const set = (changes: Partial<Place>) => onChange({ ...place, ...changes });
  const cityHint = useId();
  const first = countries.filter((c) => suggested.includes(c.id));
  const rest = countries.filter((c) => !suggested.includes(c.id));

  return (
    <div className="form-grid">
      <label className="field">
        <span>{t("cases.country")}</span>
        <select
          required
          value={place.destination}
          onChange={(e) => set({ destination: e.target.value, center: "" })}
        >
          <option value="" />
          {first.length > 0 && (
            <optgroup label={t("cases.fromConsultation")}>
              {first.map((c) => (
                <option key={c.id} value={c.id}>
                  {name.country(c)}
                </option>
              ))}
            </optgroup>
          )}
          {rest.map((c) => (
            <option key={c.id} value={c.id}>
              {name.country(c)}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>{t("cases.submissionCountry")}</span>
        <select
          required
          value={place.submission}
          onChange={(e) => set({ submission: e.target.value, center: "" })}
        >
          {countries.map((c) => (
            <option key={c.id} value={c.id}>
              {name.country(c)}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>{t("cases.city")}</span>
        <select
          value={place.center}
          disabled={centers.length === 0}
          aria-describedby={cityHint}
          onChange={(e) => set({ center: e.target.value })}
        >
          <option value="" />
          {centers.map((v) => (
            <option key={v.id} value={v.id}>
              {name.city(v)}
            </option>
          ))}
        </select>
        {place.destination && centers.length === 0 && (
          <small id={cityHint}>{t(rule ? "cases.noCities" : "cases.noRule")}</small>
        )}
      </label>
      <label className="field">
        <span>{t("cases.purpose")}</span>
        <select value={place.purpose} onChange={(e) => set({ purpose: e.target.value })}>
          <option value="" />
          {lists.purposes
            .filter((p) => p.is_active || p.id === place.purpose)
            .map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
        </select>
      </label>
      <label className="field">
        <span>{t("cases.registrarField")}</span>
        <select value={place.registrar} onChange={(e) => set({ registrar: e.target.value })}>
          <option value="" />
          {lists.registrars
            .filter((r) => r.is_active || r.id === place.registrar)
            .map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
        </select>
      </label>
      <label className="field">
        <span>{t("dict.hallLabel")}</span>
        <select value={place.hall} onChange={(e) => set({ hall: e.target.value as Hall | "" })}>
          <option value="" />
          {HALLS.map((h) => (
            <option key={h} value={h}>
              {t(`dict.hall.${h}`)}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>{t("cases.submissionOn")}</span>
        <input
          type="date"
          value={place.submissionOn}
          onChange={(e) => set({ submissionOn: e.target.value })}
        />
      </label>
    </div>
  );
}

type NewApplicant = { key: number; full_name: string; label: string };

function CaseCreateForm({
  card,
  consultation,
  onDone,
}: {
  card: ContactCard;
  consultation?: Consultation;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const countries = useCountries();
  const refresh = useRefreshCards();
  const belarus = countries.data?.find((c) => c.code === DEFAULT_SUBMISSION_CODE)?.id ?? "";
  const suggested = consultation?.destination_ids ?? [];
  const [place, setPlace] = useState<Place>({
    destination: suggested[0] ?? "",
    submission: belarus,
    center: "",
    purpose: "",
    registrar: "",
    hall: "",
    submissionOn: "",
  });
  // Belarus as the submission country once the countries are loaded.
  useEffect(() => {
    if (!place.submission && belarus) setPlace((p) => ({ ...p, submission: belarus }));
  }, [belarus, place.submission]);
  const [chosen, setChosen] = useState<Set<string>>(new Set([card.contact.id]));
  const [added, setAdded] = useState<NewApplicant[]>([]);
  const nextKey = useRef(0);

  const people = [
    { id: card.contact.id, full_name: card.contact.full_name, label: null as string | null },
    ...card.relations.map((r) => ({ id: r.related_id, full_name: r.full_name, label: r.label })),
  ];
  const named = added.filter((a) => a.full_name.trim());
  const count = chosen.size + named.length;

  const create = useMutation({
    mutationFn: () =>
      apiPost<VisaCase[]>(`/contacts/${card.contact.id}/cases`, {
        ...placeBody(place),
        consultation_id: consultation?.id ?? null,
        applicants: [
          ...people.filter((p) => chosen.has(p.id)).map((p) => ({ contact_id: p.id })),
          ...named.map((a) => ({ full_name: a.full_name.trim(), label: a.label.trim() || null })),
        ],
      }),
    onSuccess: () => {
      void refresh();
      onDone();
    },
  });

  const toggle = (id: string) =>
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <form
      className="form-card consultation-form"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <h3 className="h3">{t(consultation ? "cases.createFromConsultation" : "cases.newTitle")}</h3>
      <PlaceFields place={place} onChange={setPlace} suggested={suggested} />
      <fieldset className="module-block">
        <legend>{t("cases.applicants")}</legend>
        <p className="muted small">
          {consultation
            ? t("cases.applicantsHintConsultation", {
                planned: consultation.applicants,
                count,
              })
            : t("cases.applicantsHint")}
        </p>
        {people.map((p) => (
          <label key={p.id} className="checkbox">
            <input type="checkbox" checked={chosen.has(p.id)} onChange={() => toggle(p.id)} />
            <span>
              {p.full_name}
              {p.label && <span className="muted"> · {p.label}</span>}
            </span>
          </label>
        ))}
        {added.map((a) => (
          <div key={a.key} className="applicant-row">
            <label className="field">
              <span>{t("cases.newApplicantName")}</span>
              <input
                maxLength={200}
                value={a.full_name}
                onChange={(e) =>
                  setAdded((list) =>
                    list.map((x) => (x.key === a.key ? { ...x, full_name: e.target.value } : x)),
                  )
                }
              />
            </label>
            <label className="field">
              <span>{t("cases.newApplicantLabel")}</span>
              <input
                maxLength={100}
                value={a.label}
                onChange={(e) =>
                  setAdded((list) =>
                    list.map((x) => (x.key === a.key ? { ...x, label: e.target.value } : x)),
                  )
                }
              />
            </label>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setAdded((list) => list.filter((x) => x.key !== a.key))}
            >
              {t("dict.remove")}
            </button>
          </div>
        ))}
        <button
          type="button"
          className="btn btn-sm"
          onClick={() =>
            setAdded((list) => [...list, { key: nextKey.current++, full_name: "", label: "" }])
          }
        >
          {t("cases.addApplicant")}
        </button>
      </fieldset>
      {create.isError && <ErrorAlert error={create.error} />}
      <div className="form-actions">
        <button
          type="submit"
          className="btn btn-sm btn-p"
          disabled={create.isPending || count === 0 || !place.destination || !place.submission}
        >
          {t("cases.create", { count })}
        </button>
        <button type="button" className="btn btn-sm" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

function CaseEditForm({ visaCase: c, onDone }: { visaCase: VisaCase; onDone: () => void }) {
  const { t } = useTranslation();
  const refresh = useRefreshCards();
  const [place, setPlace] = useState<Place>({
    destination: c.destination_id,
    submission: c.submission_country_id,
    center: c.visa_center_id ?? "",
    purpose: c.purpose_id ?? "",
    registrar: c.registrar_id ?? "",
    hall: c.hall ?? "",
    submissionOn: c.submission_on ?? "",
  });
  const [outcome, setOutcome] = useState<Outcome | "">(c.outcome ?? "");
  const [decidedOn, setDecidedOn] = useState(c.decided_on ?? "");
  const [entries, setEntries] = useState<VisaEntries | "">(c.visa_entries ?? "");
  const [validFrom, setValidFrom] = useState(c.visa_valid_from ?? "");
  const [validTo, setValidTo] = useState(c.visa_valid_to ?? "");
  const [stayDays, setStayDays] = useState(c.stay_days ? String(c.stay_days) : "");
  const [afterAppeal, setAfterAppeal] = useState(c.after_appeal);
  const [vacNumber, setVacNumber] = useState(c.vac_number ?? "");
  const [email, setEmail] = useState(c.application_email ?? "");
  const [emailOwner, setEmailOwner] = useState<EmailOwner | "">(c.application_email_owner ?? "");
  const numberLabel = useNumberLabel();
  const isVisa = outcome === "visa";
  const decided = outcome === "visa" || outcome === "refusal";

  const save = useMutation({
    mutationFn: () =>
      apiPatch<VisaCase>(`/cases/${c.id}`, {
        ...placeBody(place),
        outcome: outcome || null,
        decided_on: outcome ? decidedOn || null : null,
        visa_entries: isVisa ? entries || null : null,
        visa_valid_from: isVisa ? validFrom || null : null,
        visa_valid_to: isVisa ? validTo || null : null,
        stay_days: isVisa && stayDays ? Number(stayDays) : null,
        after_appeal: decided ? afterAppeal : false,
        vac_number: vacNumber.trim() || null,
        application_email: email.trim() || null,
        application_email_owner: email.trim() ? emailOwner || null : null,
      }),
    onSuccess: () => {
      void refresh();
      onDone();
    },
  });

  return (
    <form
      className="form-card"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <PlaceFields place={place} onChange={setPlace} />
      <fieldset className="module-block">
        <legend>{t("queues.numberAndEmail")}</legend>
        <div className="form-grid">
          <label className="field">
            <span>{numberLabel(c)}</span>
            <input maxLength={64} value={vacNumber} onChange={(e) => setVacNumber(e.target.value)} />
          </label>
          <label className="field">
            <span>{t("queues.email")}</span>
            <input
              type="email"
              maxLength={320}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="field">
            <span>{t("queues.ownerLabel")}</span>
            <select
              value={emailOwner}
              onChange={(e) => setEmailOwner(e.target.value as EmailOwner | "")}
            >
              <option value="" />
              {EMAIL_OWNERS.map((o) => (
                <option key={o} value={o}>
                  {t(`queues.owner.${o}`)}
                </option>
              ))}
            </select>
          </label>
        </div>
      </fieldset>
      <fieldset className="module-block">
        <legend>{t("cases.result")}</legend>
        <div className="form-grid">
          <label className="field">
            <span>{t("cases.outcomeLabel")}</span>
            <select value={outcome} onChange={(e) => setOutcome(e.target.value as Outcome | "")}>
              <option value="">{t("cases.noOutcome")}</option>
              {OUTCOMES.map((o) => (
                <option key={o} value={o}>
                  {t(`cases.outcome.${o}`)}
                </option>
              ))}
            </select>
          </label>
          {outcome && (
            <label className="field">
              <span>{t("cases.decidedOn")}</span>
              <input type="date" value={decidedOn} onChange={(e) => setDecidedOn(e.target.value)} />
            </label>
          )}
          {isVisa && (
            <>
              <label className="field">
                <span>{t("cases.entriesLabel")}</span>
                <select
                  value={entries}
                  onChange={(e) => setEntries(e.target.value as VisaEntries | "")}
                >
                  <option value="" />
                  {VISA_ENTRIES.map((v) => (
                    <option key={v} value={v}>
                      {t(`cases.entries.${v}`)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>{t("contacts.validFrom")}</span>
                <input type="date" value={validFrom} onChange={(e) => setValidFrom(e.target.value)} />
              </label>
              <label className="field">
                <span>{t("contacts.validTo")}</span>
                <input
                  type="date"
                  min={validFrom || undefined}
                  value={validTo}
                  onChange={(e) => setValidTo(e.target.value)}
                />
              </label>
              <label className="field">
                <span>{t("cases.stayDays")}</span>
                <input
                  type="number"
                  min={1}
                  max={3660}
                  value={stayDays}
                  onChange={(e) => setStayDays(e.target.value)}
                />
              </label>
            </>
          )}
        </div>
        {decided && (
          <label className="checkbox">
            <input
              type="checkbox"
              checked={afterAppeal}
              onChange={(e) => setAfterAppeal(e.target.checked)}
            />
            <span>{t("cases.afterAppealLabel")}</span>
          </label>
        )}
      </fieldset>
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

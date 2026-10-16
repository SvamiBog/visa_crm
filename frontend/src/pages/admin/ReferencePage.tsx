import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { apiPatch, apiPost, errorCode } from "../../api";
import { AppealsLabel, BookingLabel, OperatorLabel } from "../../components/RuleLabels";
import {
  BOOKING_TYPES,
  DEFAULT_SUBMISSION_CODE,
  IDENTIFIER_TYPES,
  byId,
  useCountries,
  useLocalName,
  useOperators,
  useRules,
  type BookingType,
  type Country,
  type IdentifierType,
  type Operator,
  type Rule,
} from "../../reference";

type Tab = "rules" | "countries" | "operators";
const TABS: Tab[] = ["rules", "countries", "operators"];

/** Admin: the shared reference of countries, operators and appointment rules. */
export function ReferencePage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("rules");
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("reference.title")}</h1>
          <div className="muted">{t("reference.subtitleAdmin")}</div>
        </div>
      </header>
      <div className="tabs" role="tablist">
        {TABS.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "tab active" : "tab"}
            onClick={() => setTab(key)}
          >
            {t(`reference.tab.${key}`)}
          </button>
        ))}
      </div>
      {tab === "rules" && <RulesTab />}
      {tab === "countries" && <CountriesTab />}
      {tab === "operators" && <OperatorsTab />}
    </div>
  );
}

function useRefreshReference() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["reference"] });
}

function ErrorAlert({ error }: { error: unknown }) {
  const { t } = useTranslation();
  return (
    <div className="alert alert-error" role="alert">
      {t(`errors.${errorCode(error)}`, { defaultValue: t("errors.unknown") })}
    </div>
  );
}

// --- Rules ---------------------------------------------------------------------------

function RulesTab() {
  const { t } = useTranslation();
  const name = useLocalName();
  const countries = useCountries(true);
  const operators = useOperators(true);
  const [submissionId, setSubmissionId] = useState("");
  const [editing, setEditing] = useState<Rule | "new" | null>(null);

  // Start with Belarus once the countries are loaded.
  useEffect(() => {
    if (submissionId || !countries.data) return;
    const home = countries.data.find((c) => c.code === DEFAULT_SUBMISSION_CODE);
    if (home) setSubmissionId(home.id);
  }, [countries.data, submissionId]);

  const rules = useRules(true, submissionId || undefined);
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const operatorById = useMemo(() => byId(operators.data), [operators.data]);
  const sorted = useMemo(
    () =>
      [...(countries.data ?? [])].sort((a, b) => name.country(a).localeCompare(name.country(b))),
    [countries.data, name],
  );

  return (
    <>
      <div className="toolbar">
        <label className="field field-inline">
          <span>{t("reference.submissionCountry")}</span>
          <select
            value={submissionId}
            onChange={(e) => {
              setSubmissionId(e.target.value);
              setEditing(null);
            }}
          >
            {sorted.map((c) => (
              <option key={c.id} value={c.id}>
                {name.country(c)}
              </option>
            ))}
          </select>
        </label>
        {editing === null && (
          <button type="button" className="btn btn-p" onClick={() => setEditing("new")}>
            {t("reference.addRule")}
          </button>
        )}
      </div>

      {editing !== null && submissionId && (
        <RuleForm
          key={editing === "new" ? "new" : editing.id}
          rule={editing === "new" ? undefined : editing}
          submissionId={submissionId}
          countries={sorted}
          operators={operators.data ?? []}
          taken={new Set((rules.data ?? []).map((r) => r.destination_id))}
          onClose={() => setEditing(null)}
        />
      )}

      <section className="card">
        {rules.isPending && <p className="muted">{t("common.loading")}</p>}
        {rules.isError && <ErrorAlert error={rules.error} />}
        {rules.data?.length === 0 && <p className="muted">{t("reference.noRules")}</p>}
        {rules.data && rules.data.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("reference.colDestination")}</th>
                  <th>{t("reference.colBooking")}</th>
                  <th>{t("reference.colOperator")}</th>
                  <th>{t("reference.colIdentifier")}</th>
                  <th>{t("reference.colAppeals")}</th>
                  <th>{t("reference.colCities")}</th>
                  <th>{t("admin.colStatus")}</th>
                  <th aria-label={t("admin.colActions")} />
                </tr>
              </thead>
              <tbody>
                {rules.data.map((rule) => (
                  <tr key={rule.id} className={rule.is_active ? undefined : "row-off"}>
                    <td className="nowrap">{name.country(countryById.get(rule.destination_id))}</td>
                    <td>
                      <BookingLabel rule={rule} />
                    </td>
                    <td>
                      <OperatorLabel
                        rule={rule}
                        operator={operatorById.get(rule.vac_operator_id ?? "")}
                        represented={countryById.get(rule.represented_by_id ?? "")}
                      />
                    </td>
                    <td className="muted">
                      {rule.identifier_type ? t(`reference.identifier.${rule.identifier_type}`) : "—"}
                    </td>
                    <td className="muted">
                      <AppealsLabel rule={rule} />
                    </td>
                    <td className="muted">
                      {rule.visa_centers.map((v) => name.city(v)).join(", ") || "—"}
                    </td>
                    <td>
                      <RuleStatus rule={rule} />
                    </td>
                    <td>
                      <div className="actions">
                        <button type="button" className="btn btn-sm" onClick={() => setEditing(rule)}>
                          {t("reference.edit")}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function RuleStatus({ rule }: { rule: Rule }) {
  const { t } = useTranslation();
  if (!rule.is_active) return <span className="chip st-gray">{t("reference.off")}</span>;
  if (!rule.is_verified) return <span className="chip st-orange">{t("reference.unverified")}</span>;
  return <span className="chip st-green">{t("reference.verified")}</span>;
}

type Tri = "unknown" | "yes" | "no";

function RuleForm({
  rule,
  submissionId,
  countries,
  operators,
  taken,
  onClose,
}: {
  rule?: Rule;
  submissionId: string;
  countries: Country[];
  operators: Operator[];
  taken: Set<string>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const name = useLocalName();
  const refresh = useRefreshReference();
  const [destinationId, setDestinationId] = useState(rule?.destination_id ?? "");
  const [booking, setBooking] = useState<BookingType>(rule?.booking_type ?? "direct_slot");
  const [windowDays, setWindowDays] = useState(String(rule?.window_days ?? 60));
  const [operatorId, setOperatorId] = useState(rule?.vac_operator_id ?? "");
  const [representedId, setRepresentedId] = useState(rule?.represented_by_id ?? "");
  const [identifier, setIdentifier] = useState<IdentifierType | "">(rule?.identifier_type ?? "");
  const [appeals, setAppeals] = useState<Tri>(
    rule?.appeals_accepted == null ? "unknown" : rule.appeals_accepted ? "yes" : "no",
  );
  const [appealDays, setAppealDays] = useState(String(rule?.appeal_deadline_days ?? ""));
  const [verified, setVerified] = useState(rule?.is_verified ?? false);
  const [active, setActive] = useState(rule?.is_active ?? true);
  const [notes, setNotes] = useState(rule?.notes ?? "");

  const destinations = countries.filter(
    (c) => c.id !== submissionId && (c.id === rule?.destination_id || !taken.has(c.id)),
  );

  const save = useMutation({
    mutationFn: () => {
      const fields = {
        booking_type: booking,
        window_days: booking === "windowed_application" ? Number(windowDays) : null,
        vac_operator_id: operatorId || null,
        represented_by_id: representedId || null,
        identifier_type: identifier || null,
        appeals_accepted: appeals === "unknown" ? null : appeals === "yes",
        appeal_deadline_days: appeals === "yes" && appealDays ? Number(appealDays) : null,
        is_verified: verified,
        is_active: active,
        notes: notes.trim() || null,
      };
      return rule
        ? apiPatch<Rule>(`/admin/reference/rules/${rule.id}`, fields)
        : apiPost<Rule>("/admin/reference/rules", {
            ...fields,
            destination_id: destinationId,
            submission_id: submissionId,
          });
    },
    onSuccess: () => {
      void refresh();
      onClose();
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form className="card form-card" onSubmit={submit}>
      <h2 className="h2">
        {rule
          ? t("reference.editRule", {
              country: name.country(countries.find((c) => c.id === rule.destination_id)),
            })
          : t("reference.newRule")}
      </h2>
      <div className="form-grid">
        {!rule && (
          <label className="field">
            <span>{t("reference.colDestination")}</span>
            <select required value={destinationId} onChange={(e) => setDestinationId(e.target.value)}>
              <option value="" />
              {destinations.map((c) => (
                <option key={c.id} value={c.id}>
                  {name.country(c)}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span>{t("reference.colBooking")}</span>
          <select value={booking} onChange={(e) => setBooking(e.target.value as BookingType)}>
            {BOOKING_TYPES.map((b) => (
              <option key={b} value={b}>
                {t(`reference.booking.${b}`)}
              </option>
            ))}
          </select>
        </label>
        {booking === "windowed_application" && (
          <label className="field">
            <span>{t("reference.windowLabel")}</span>
            <input
              type="number"
              min={1}
              max={365}
              required
              value={windowDays}
              onChange={(e) => setWindowDays(e.target.value)}
            />
          </label>
        )}
        <label className="field">
          <span>{t("reference.colOperator")}</span>
          <select value={operatorId} onChange={(e) => setOperatorId(e.target.value)}>
            <option value="">{t("reference.embassy")}</option>
            {operators.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("reference.representedBy")}</span>
          <select value={representedId} onChange={(e) => setRepresentedId(e.target.value)}>
            <option value="">{t("reference.notRepresented")}</option>
            {countries
              .filter((c) => c.id !== submissionId)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {name.country(c)}
                </option>
              ))}
          </select>
        </label>
        <label className="field">
          <span>{t("reference.colIdentifier")}</span>
          <select
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value as IdentifierType | "")}
          >
            <option value="">{t("reference.unknown")}</option>
            {IDENTIFIER_TYPES.map((i) => (
              <option key={i} value={i}>
                {t(`reference.identifier.${i}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("reference.appealsLabel")}</span>
          <select value={appeals} onChange={(e) => setAppeals(e.target.value as Tri)}>
            <option value="unknown">{t("reference.unknown")}</option>
            <option value="yes">{t("reference.appealsYes")}</option>
            <option value="no">{t("reference.appealsNo")}</option>
          </select>
        </label>
        {appeals === "yes" && (
          <label className="field">
            <span>{t("reference.appealDaysLabel")}</span>
            <input
              type="number"
              min={1}
              max={365}
              value={appealDays}
              onChange={(e) => setAppealDays(e.target.value)}
            />
          </label>
        )}
      </div>
      <label className="field">
        <span>{t("reference.notes")}</span>
        <input maxLength={1000} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </label>
      <div className="form-actions">
        <label className="checkbox">
          <input type="checkbox" checked={verified} onChange={(e) => setVerified(e.target.checked)} />
          {t("reference.verifiedLabel")}
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          {t("reference.activeLabel")}
        </label>
      </div>
      {rule && <VisaCenters rule={rule} />}
      {save.isError && <ErrorAlert error={save.error} />}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={save.isPending || (!rule && !destinationId)}>
          {t("common.save")}
        </button>
        <button type="button" className="btn" onClick={onClose}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

/** Cities of a rule: each has its own queue. Saved at once, apart from the rule form. */
function VisaCenters({ rule }: { rule: Rule }) {
  const { t } = useTranslation();
  const refresh = useRefreshReference();
  const [centers, setCenters] = useState(rule.visa_centers);
  const [cityRu, setCityRu] = useState("");
  const [cityEn, setCityEn] = useState("");

  const add = useMutation({
    mutationFn: () =>
      apiPost<Rule["visa_centers"][number]>(`/admin/reference/rules/${rule.id}/visa-centers`, {
        city_ru: cityRu.trim(),
        city_en: cityEn.trim(),
      }),
    onSuccess: (center) => {
      setCenters((list) => [...list, center]);
      setCityRu("");
      setCityEn("");
      void refresh();
    },
  });
  const toggle = useMutation({
    mutationFn: (center: Rule["visa_centers"][number]) =>
      apiPatch<Rule["visa_centers"][number]>(`/admin/reference/visa-centers/${center.id}`, {
        is_active: !center.is_active,
      }),
    onSuccess: (updated) => {
      setCenters((list) => list.map((c) => (c.id === updated.id ? updated : c)));
      void refresh();
    },
  });

  return (
    <fieldset className="module-block">
      <legend>{t("reference.colCities")}</legend>
      <p className="muted small">{t("reference.citiesHint")}</p>
      {centers.length > 0 && (
        <ul className="plain-list">
          {centers.map((c) => (
            <li key={c.id} className={c.is_active ? undefined : "row-off"}>
              <span>
                {c.city_ru} · {c.city_en}
              </span>
              <button
                type="button"
                className="btn btn-sm"
                disabled={toggle.isPending}
                onClick={() => toggle.mutate(c)}
              >
                {t(c.is_active ? "reference.switchOff" : "reference.switchOn")}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="form-grid">
        <label className="field">
          <span>{t("reference.cityRu")}</span>
          <input value={cityRu} maxLength={100} onChange={(e) => setCityRu(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("reference.cityEn")}</span>
          <input value={cityEn} maxLength={100} onChange={(e) => setCityEn(e.target.value)} />
        </label>
      </div>
      {add.isError && <ErrorAlert error={add.error} />}
      <div className="form-actions">
        <button
          type="button"
          className="btn btn-sm"
          disabled={add.isPending || !cityRu.trim() || !cityEn.trim()}
          onClick={() => add.mutate()}
        >
          {t("reference.addCity")}
        </button>
      </div>
    </fieldset>
  );
}

// --- Countries -----------------------------------------------------------------------

function CountriesTab() {
  const { t } = useTranslation();
  const name = useLocalName();
  const countries = useCountries(true);
  const [editing, setEditing] = useState<Country | "new" | null>(null);
  const sorted = useMemo(
    () =>
      [...(countries.data ?? [])].sort((a, b) => name.country(a).localeCompare(name.country(b))),
    [countries.data, name],
  );

  return (
    <>
      <div className="toolbar">
        <p className="muted small">{t("reference.countriesHint")}</p>
        {editing === null && (
          <button type="button" className="btn btn-p" onClick={() => setEditing("new")}>
            {t("reference.addCountry")}
          </button>
        )}
      </div>
      {editing !== null && (
        <CountryForm
          key={editing === "new" ? "new" : editing.id}
          country={editing === "new" ? undefined : editing}
          onClose={() => setEditing(null)}
        />
      )}
      <section className="card">
        {countries.isPending && <p className="muted">{t("common.loading")}</p>}
        {countries.isError && <ErrorAlert error={countries.error} />}
        {sorted.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("reference.colCode")}</th>
                  <th>{t("reference.nameRu")}</th>
                  <th>{t("reference.nameEn")}</th>
                  <th>{t("reference.consultationMinutes")}</th>
                  <th>{t("reference.passportRule")}</th>
                  <th>{t("admin.colStatus")}</th>
                  <th aria-label={t("admin.colActions")} />
                </tr>
              </thead>
              <tbody>
                {sorted.map((c) => (
                  <tr key={c.id} className={c.is_active ? undefined : "row-off"}>
                    <td className="mono">{c.code}</td>
                    <td>{c.name_ru}</td>
                    <td>{c.name_en}</td>
                    <td className="muted">{c.consultation_minutes ?? "—"}</td>
                    <td className="muted">
                      {c.passport_max_age_years !== null || c.passport_valid_after_months !== null
                        ? t("reference.passportRuleValue", {
                            years: c.passport_max_age_years ?? "—",
                            months: c.passport_valid_after_months ?? "—",
                          })
                        : "—"}
                    </td>
                    <td>
                      {c.is_active ? (
                        <span className="chip st-green">{t("reference.on")}</span>
                      ) : (
                        <span className="chip st-gray">{t("reference.off")}</span>
                      )}
                    </td>
                    <td>
                      <div className="actions">
                        <button type="button" className="btn btn-sm" onClick={() => setEditing(c)}>
                          {t("reference.edit")}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function CountryForm({ country, onClose }: { country?: Country; onClose: () => void }) {
  const { t } = useTranslation();
  const refresh = useRefreshReference();
  const [code, setCode] = useState(country?.code ?? "");
  const [nameRu, setNameRu] = useState(country?.name_ru ?? "");
  const [nameEn, setNameEn] = useState(country?.name_en ?? "");
  const [minutes, setMinutes] = useState(String(country?.consultation_minutes ?? ""));
  const [maxAge, setMaxAge] = useState(String(country?.passport_max_age_years ?? ""));
  const [validAfter, setValidAfter] = useState(String(country?.passport_valid_after_months ?? ""));
  const [active, setActive] = useState(country?.is_active ?? true);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        code,
        name_ru: nameRu.trim(),
        name_en: nameEn.trim(),
        consultation_minutes: minutes ? Number(minutes) : null,
        passport_max_age_years: maxAge ? Number(maxAge) : null,
        passport_valid_after_months: validAfter ? Number(validAfter) : null,
        is_active: active,
      };
      return country
        ? apiPatch<Country>(`/admin/reference/countries/${country.id}`, body)
        : apiPost<Country>("/admin/reference/countries", body);
    },
    onSuccess: () => {
      void refresh();
      onClose();
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form className="card form-card" onSubmit={submit}>
      <h2 className="h2">{country ? country.name_ru : t("reference.newCountry")}</h2>
      <div className="form-grid">
        <label className="field">
          <span>{t("reference.colCode")}</span>
          <input
            required
            pattern="[A-Za-z]{2}"
            maxLength={2}
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            aria-describedby="country-code-hint"
          />
        </label>
        <label className="field">
          <span>{t("reference.nameRu")}</span>
          <input required maxLength={100} value={nameRu} onChange={(e) => setNameRu(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("reference.nameEn")}</span>
          <input required maxLength={100} value={nameEn} onChange={(e) => setNameEn(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("reference.consultationMinutes")}</span>
          <input
            type="number"
            min={5}
            max={600}
            value={minutes}
            onChange={(e) => setMinutes(e.target.value)}
          />
        </label>
        <label className="field">
          <span>{t("reference.passportMaxAge")}</span>
          <input
            type="number"
            min={1}
            max={30}
            value={maxAge}
            onChange={(e) => setMaxAge(e.target.value)}
          />
        </label>
        <label className="field">
          <span>{t("reference.passportValidAfter")}</span>
          <input
            type="number"
            min={0}
            max={24}
            value={validAfter}
            onChange={(e) => setValidAfter(e.target.value)}
          />
        </label>
      </div>
      <p className="muted small">{t("reference.passportHint")}</p>
      <p id="country-code-hint" className="muted small">
        {t("reference.codeHint")}
      </p>
      <label className="checkbox">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        {t("reference.activeLabel")}
      </label>
      {save.isError && <ErrorAlert error={save.error} />}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={save.isPending}>
          {t("common.save")}
        </button>
        <button type="button" className="btn" onClick={onClose}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

// --- Operators -----------------------------------------------------------------------

function OperatorsTab() {
  const { t } = useTranslation();
  const refresh = useRefreshReference();
  const operators = useOperators(true);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");

  const add = useMutation({
    mutationFn: () => apiPost<Operator>("/admin/reference/operators", { code, name: name.trim() }),
    onSuccess: () => {
      setCode("");
      setName("");
      void refresh();
    },
  });
  const toggle = useMutation({
    mutationFn: (o: Operator) =>
      apiPatch<Operator>(`/admin/reference/operators/${o.id}`, { is_active: !o.is_active }),
    onSuccess: () => void refresh(),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    add.mutate();
  };

  return (
    <>
      <section className="card">
        {operators.isPending && <p className="muted">{t("common.loading")}</p>}
        {operators.data && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("reference.operatorName")}</th>
                  <th>{t("reference.colCode")}</th>
                  <th>{t("admin.colStatus")}</th>
                  <th aria-label={t("admin.colActions")} />
                </tr>
              </thead>
              <tbody>
                {operators.data.map((o) => (
                  <tr key={o.id} className={o.is_active ? undefined : "row-off"}>
                    <td>{o.name}</td>
                    <td className="mono muted">{o.code}</td>
                    <td>
                      {o.is_active ? (
                        <span className="chip st-green">{t("reference.on")}</span>
                      ) : (
                        <span className="chip st-gray">{t("reference.off")}</span>
                      )}
                    </td>
                    <td>
                      <div className="actions">
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={toggle.isPending}
                          onClick={() => toggle.mutate(o)}
                        >
                          {t(o.is_active ? "reference.switchOff" : "reference.switchOn")}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {toggle.isError && <ErrorAlert error={toggle.error} />}
      </section>
      <form className="card form-card" onSubmit={submit}>
        <h2 className="h2">{t("reference.newOperator")}</h2>
        <div className="form-grid">
          <label className="field">
            <span>{t("reference.operatorName")}</span>
            <input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>{t("reference.colCode")}</span>
            <input
              required
              pattern="[a-z0-9_]{2,32}"
              maxLength={32}
              value={code}
              onChange={(e) => setCode(e.target.value.toLowerCase())}
              aria-describedby="operator-code-hint"
            />
          </label>
        </div>
        <p id="operator-code-hint" className="muted small">
          {t("reference.operatorCodeHint")}
        </p>
        {add.isError && <ErrorAlert error={add.error} />}
        <div className="form-actions">
          <button type="submit" className="btn btn-p" disabled={add.isPending}>
            {t("reference.addOperator")}
          </button>
        </div>
      </form>
    </>
  );
}

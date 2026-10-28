import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type FormEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { apiDelete, apiPatch, apiPost, errorCode } from "../../api";
import { useMe } from "../../auth";
import { useDisclosure } from "../../components/Disclosure";
import { AppealsLabel, BookingLabel, OperatorLabel } from "../../components/RuleLabels";
import {
  BOOKING_TYPES,
  DEFAULT_SUBMISSION_CODE,
  IDENTIFIER_TYPES,
  RULES_KEY,
  byId,
  useAllRules,
  useCountries,
  useLocalName,
  useOperators,
  type BookingType,
  type Country,
  type IdentifierType,
  type Operator,
  type Rule,
  type VisaCenter,
} from "../../reference";
import { ErrorAlert } from "../clients/ui";

type Editing = { mode: "new" } | { mode: "edit"; rule: Rule } | { mode: "copy"; rule: Rule };

/** Workspace: the specialist's directions — how she books each destination from each
 * submission country, and where it is submitted (backlog 1.20). Employees read them. */
export function CountriesPage() {
  const { t } = useTranslation();
  const me = useMe();
  const canEdit = me.data?.role === "specialist";
  const name = useLocalName();
  const countries = useCountries();
  const operators = useOperators();
  const allRules = useAllRules();
  const [submissionId, setSubmissionId] = useState("");
  const [editing, setEditing] = useState<Editing | null>(null);

  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const operatorById = useMemo(() => byId(operators.data), [operators.data]);
  const sortedCountries = useMemo(
    () =>
      [...(countries.data ?? [])].sort((a, b) => name.country(a).localeCompare(name.country(b))),
    [countries.data, name],
  );
  // Tabs: the submission countries she has directions from.
  const submissions = useMemo(() => {
    const ids = new Set((allRules.data ?? []).map((r) => r.submission_id));
    return sortedCountries.filter((c) => ids.has(c.id));
  }, [allRules.data, sortedCountries]);

  useEffect(() => {
    if (submissions.length === 0) return;
    if (submissions.some((c) => c.id === submissionId)) return;
    const home = submissions.find((c) => c.code === DEFAULT_SUBMISSION_CODE) ?? submissions[0];
    setSubmissionId(home.id);
  }, [submissions, submissionId]);

  const rules = (allRules.data ?? [])
    .filter((r) => r.submission_id === submissionId)
    .sort((a, b) =>
      name
        .country(countryById.get(a.destination_id))
        .localeCompare(name.country(countryById.get(b.destination_id))),
    );
  const home = sortedCountries.find((c) => c.code === DEFAULT_SUBMISSION_CODE);

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("directions.title")}</h1>
          <div className="muted">{t("directions.subtitle")}</div>
        </div>
        {canEdit && editing === null && (
          <button type="button" className="btn btn-p" onClick={() => setEditing({ mode: "new" })}>
            {t("directions.add")}
          </button>
        )}
      </header>

      {submissions.length > 1 && (
        <div className="tabs" role="tablist" aria-label={t("reference.submissionCountry")}>
          {submissions.map((c) => (
            <button
              key={c.id}
              type="button"
              role="tab"
              aria-selected={c.id === submissionId}
              className={c.id === submissionId ? "tab active" : "tab"}
              onClick={() => setSubmissionId(c.id)}
            >
              {t("directions.from", { country: name.country(c) })}
              <span className="tab-count">
                {(allRules.data ?? []).filter((r) => r.submission_id === c.id).length}
              </span>
            </button>
          ))}
        </div>
      )}

      {editing !== null && (
        <DirectionForm
          key={editing.mode === "new" ? "new" : `${editing.mode}-${editing.rule.id}`}
          editing={editing}
          submissionId={submissionId || home?.id || ""}
          countries={sortedCountries}
          operators={operators.data ?? []}
          rules={allRules.data ?? []}
          onClose={(savedSubmission) => {
            setEditing(null);
            if (savedSubmission) setSubmissionId(savedSubmission);
          }}
        />
      )}

      <section className="card">
        {allRules.isPending && <p className="muted">{t("common.loading")}</p>}
        {allRules.isError && <ErrorAlert error={allRules.error} />}
        {allRules.data?.length === 0 && (
          <p className="muted">{t(canEdit ? "directions.empty" : "directions.emptyReader")}</p>
        )}
        {submissions.length === 1 && (
          <h2 className="h2">{t("directions.from", { country: name.country(submissions[0]) })}</h2>
        )}
        {rules.length > 0 && (
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
                  {canEdit && <th aria-label={t("admin.colActions")} />}
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <tr key={rule.id} className={rule.is_active ? undefined : "row-off"}>
                    <td className="nowrap">
                      {name.country(countryById.get(rule.destination_id))}
                      {!rule.is_active && (
                        <div>
                          <span className="chip st-gray">{t("reference.off")}</span>
                        </div>
                      )}
                    </td>
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
                      {rule.visa_centers
                        .filter((v) => v.is_active)
                        .map((v) => name.city(v))
                        .join(", ") || "—"}
                    </td>
                    {canEdit && (
                      <td>
                        <div className="actions actions-row">
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => setEditing({ mode: "edit", rule })}
                          >
                            {t("reference.edit")}
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm"
                            aria-label={t("directions.copyFor", {
                              country: name.country(countryById.get(rule.destination_id)),
                            })}
                            onClick={() => setEditing({ mode: "copy", rule })}
                          >
                            {t("directions.copy")}
                          </button>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {!canEdit && allRules.data && allRules.data.length > 0 && (
        <p className="muted small">{t("directions.readerHint")}</p>
      )}
    </div>
  );
}

function useRefreshRules() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: RULES_KEY });
}

type Tri = "unknown" | "yes" | "no";

/** A new direction (for one or several destinations), a copy of one, or a change. */
function DirectionForm({
  editing,
  submissionId,
  countries,
  operators,
  rules,
  onClose,
}: {
  editing: Editing;
  submissionId: string;
  countries: Country[];
  operators: Operator[];
  rules: Rule[];
  onClose: (savedSubmission?: string) => void;
}) {
  const { t } = useTranslation();
  const name = useLocalName();
  const refresh = useRefreshRules();
  const rule = editing.mode === "new" ? undefined : editing.rule;
  const existing = editing.mode === "edit" ? editing.rule : undefined;
  const more = useDisclosure(
    !!rule && (rule.appeals_accepted !== null || !!rule.notes || !!rule.identifier_type),
  );

  const [destinations, setDestinations] = useState<string[]>([]);
  const [submission, setSubmission] = useState(rule?.submission_id ?? submissionId);
  const [booking, setBooking] = useState<BookingType>(rule?.booking_type ?? "direct_slot");
  const [windowDays, setWindowDays] = useState(String(rule?.window_days ?? 60));
  const [operatorId, setOperatorId] = useState(rule?.vac_operator_id ?? "");
  const [representedId, setRepresentedId] = useState(rule?.represented_by_id ?? "");
  const [identifier, setIdentifier] = useState<IdentifierType | "">(rule?.identifier_type ?? "");
  const [appeals, setAppeals] = useState<Tri>(
    rule?.appeals_accepted == null ? "unknown" : rule.appeals_accepted ? "yes" : "no",
  );
  const [appealDays, setAppealDays] = useState(String(rule?.appeal_deadline_days ?? ""));
  const [notes, setNotes] = useState(rule?.notes ?? "");
  const [active, setActive] = useState(rule?.is_active ?? true);
  // A new direction or a copy takes its cities along; a saved one changes them at once.
  const [cities, setCities] = useState<string[]>(
    editing.mode === "copy"
      ? editing.rule.visa_centers.filter((v) => v.is_active).map((v) => v.city_ru)
      : [],
  );

  const taken = new Set(
    rules.filter((r) => r.submission_id === submission).map((r) => r.destination_id),
  );
  const destinationChoices = countries.filter((c) => c.id !== submission && !taken.has(c.id));
  const otherCities = citiesOf(rules, submission, existing?.id);

  const fields = () => ({
    booking_type: booking,
    window_days: booking === "windowed_application" ? Number(windowDays) : null,
    vac_operator_id: operatorId || null,
    represented_by_id: representedId || null,
    identifier_type: identifier || null,
    appeals_accepted: appeals === "unknown" ? null : appeals === "yes",
    appeal_deadline_days: appeals === "yes" && appealDays ? Number(appealDays) : null,
    notes: notes.trim() || null,
    is_active: active,
  });

  const save = useMutation({
    mutationFn: (): Promise<unknown> =>
      existing
        ? apiPatch<Rule>(`/reference/rules/${existing.id}`, fields())
        : apiPost<Rule[]>("/reference/rules", {
            ...fields(),
            destination_ids: destinations,
            submission_id: submission,
            cities,
          }),
    onSuccess: () => {
      void refresh();
      onClose(submission);
    },
  });

  const remove = useMutation({
    mutationFn: (rule: Rule) => apiDelete<void>(`/reference/rules/${rule.id}`),
    onSuccess: () => {
      void refresh();
      onClose();
    },
  });
  const switchOff = useMutation({
    mutationFn: (rule: Rule) => apiPatch<Rule>(`/reference/rules/${rule.id}`, { is_active: false }),
    onSuccess: () => {
      void refresh();
      onClose();
    },
  });

  const deleteDirection = (rule: Rule) => {
    const country = name.country(countries.find((c) => c.id === rule.destination_id));
    if (!window.confirm(t("directions.confirmDelete", { country }))) return;
    remove.mutate(rule, {
      onError: (error) => {
        if (errorCode(error) === "rule_in_use" && window.confirm(t("directions.inUseSwitchOff"))) {
          switchOff.mutate(rule);
        }
      },
    });
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  const countryName = (id: string) => name.country(countries.find((c) => c.id === id));

  return (
    <form className="card form-card" onSubmit={submit}>
      <h2 className="h2">
        {existing
          ? t("directions.editTitle", {
              country: countryName(existing.destination_id),
              submission: countryName(existing.submission_id),
            })
          : editing.mode === "copy"
            ? t("directions.copyTitle", { country: countryName(editing.rule.destination_id) })
            : t("directions.newTitle")}
      </h2>

      {!existing && (
        <div className="form-grid">
          <DestinationPicker
            value={destinations}
            choices={destinationChoices}
            onChange={setDestinations}
          />
          <label className="field">
            <span>{t("reference.submissionCountry")}</span>
            <select
              required
              value={submission}
              onChange={(e) => {
                setSubmission(e.target.value);
                setDestinations([]);
              }}
            >
              <option value="" />
              {countries.map((c) => (
                <option key={c.id} value={c.id}>
                  {name.country(c)}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      <fieldset className="booking-options">
        <legend>{t("reference.colBooking")}</legend>
        {BOOKING_TYPES.map((b) => (
          <label key={b} className="booking-option">
            <input
              type="radio"
              name="booking_type"
              value={b}
              checked={booking === b}
              onChange={() => setBooking(b)}
            />
            <span>
              <span className="booking-option-title">{t(`reference.booking.${b}`)}</span>
              <span className="muted small">{t(`directions.bookingHint.${b}`)}</span>
            </span>
          </label>
        ))}
      </fieldset>

      <div className="form-grid">
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
              .filter((c) => c.id !== submission)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {name.country(c)}
                </option>
              ))}
          </select>
        </label>
      </div>

      {existing ? (
        <RuleCities rule={existing} suggestions={otherCities} />
      ) : (
        <CitiesField
          value={cities}
          onAdd={(added) => setCities((list) => [...list, ...newCities(list, added)])}
          onRemove={(city) => setCities((list) => list.filter((c) => c !== city))}
          suggestions={otherCities}
        />
      )}

      <div>
        <button className="disclosure-title" {...more.button}>
          {t("directions.more")}
        </button>
        <div className="form-grid" {...more.panel}>
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
          <label className="field">
            <span>{t("reference.notes")}</span>
            <input maxLength={1000} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </label>
        </div>
      </div>

      {existing && (
        <label className="checkbox">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          {t("reference.activeLabel")}
        </label>
      )}

      {save.isError && <ErrorAlert error={save.error} />}
      {remove.isError && errorCode(remove.error) !== "rule_in_use" && (
        <ErrorAlert error={remove.error} />
      )}
      {switchOff.isError && <ErrorAlert error={switchOff.error} />}
      <div className="form-actions">
        <button
          type="submit"
          className="btn btn-p"
          disabled={save.isPending || (!existing && (destinations.length === 0 || !submission))}
        >
          {existing
            ? t("common.save")
            : t("directions.create", { count: Math.max(destinations.length, 1) })}
        </button>
        <button type="button" className="btn" onClick={() => onClose()}>
          {t("common.cancel")}
        </button>
        {existing && (
          <button
            type="button"
            className="btn btn-danger"
            disabled={remove.isPending || switchOff.isPending}
            onClick={() => deleteDirection(existing)}
          >
            {t("directions.delete")}
          </button>
        )}
      </div>
    </form>
  );
}

/** Several destinations at once: the same booking for each, e.g. via the German embassy. */
function DestinationPicker({
  value,
  choices,
  onChange,
}: {
  value: string[];
  choices: Country[];
  onChange: (value: string[]) => void;
}) {
  const { t } = useTranslation();
  const name = useLocalName();
  const byCountry = useMemo(() => byId(choices), [choices]);
  return (
    <div className="field">
      <span>{t("directions.destinations")}</span>
      {value.length > 0 && (
        <div className="chips">
          {value.map((id) => (
            <span key={id} className="chip st-gray chip-removable">
              {name.country(byCountry.get(id))}
              <button
                type="button"
                aria-label={t("directions.removeDestination", {
                  country: name.country(byCountry.get(id)),
                })}
                onClick={() => onChange(value.filter((v) => v !== id))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <select
        aria-label={t("directions.destinations")}
        value=""
        onChange={(e) => e.target.value && onChange([...value, e.target.value])}
      >
        <option value="">{t("directions.addDestination")}</option>
        {choices
          .filter((c) => !value.includes(c.id))
          .map((c) => (
            <option key={c.id} value={c.id}>
              {name.country(c)}
            </option>
          ))}
      </select>
      <span className="muted small">{t("directions.destinationsHint")}</span>
    </div>
  );
}

/** Cities of the workspace's other directions from the same submission country: one click
 * adds them, «as for …» copies a whole list. */
type CitySuggestions = {
  cities: string[];
  byDirection: { destinationId: string; cities: string[] }[];
};

function citiesOf(rules: Rule[], submission: string, except?: string): CitySuggestions {
  const others = rules.filter((r) => r.submission_id === submission && r.id !== except);
  const cities: string[] = [];
  for (const r of others) {
    cities.push(...newCities(cities, r.visa_centers.filter((v) => v.is_active).map((v) => v.city_ru)));
  }
  return {
    cities,
    byDirection: others
      .filter((r) => r.visa_centers.some((v) => v.is_active))
      .map((r) => ({
        destinationId: r.destination_id,
        cities: r.visa_centers.filter((v) => v.is_active).map((v) => v.city_ru),
      })),
  };
}

/** «Минск» and «минск» are one city (as on the server, app/core/directions.py). */
function cityKey(city: string): string {
  return city.toLowerCase().replace(/ё/g, "е").split(/\s+/).filter(Boolean).join(" ");
}

function newCities(existing: string[], wanted: string[]): string[] {
  const seen = new Set(existing.map(cityKey));
  const added: string[] = [];
  for (const city of wanted) {
    const tidy = city.split(/\s+/).filter(Boolean).join(" ");
    if (tidy && !seen.has(cityKey(tidy))) {
      seen.add(cityKey(tidy));
      added.push(tidy);
    }
  }
  return added;
}

/** Typing a city, picking one used elsewhere or copying another direction's list. */
function CityInput({
  have,
  suggestions,
  onAdd,
  busy = false,
}: {
  have: string[];
  suggestions: CitySuggestions;
  onAdd: (cities: string[]) => void;
  busy?: boolean;
}) {
  const { t } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const [city, setCity] = useState("");
  const add = () => {
    if (!city.trim()) return;
    onAdd([city]);
    setCity("");
  };
  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    add();
  };
  const offered = newCities(have, suggestions.cities);
  return (
    <>
      <div className="input-action">
        <input
          aria-label={t("directions.city")}
          placeholder={t("directions.cityPlaceholder")}
          maxLength={100}
          value={city}
          onChange={(e) => setCity(e.target.value)}
          onKeyDown={onKey}
        />
        <button type="button" className="btn" disabled={busy || !city.trim()} onClick={add}>
          {t("reference.addCity")}
        </button>
      </div>
      {offered.length > 0 && (
        <div className="chips">
          <span className="muted small">{t("directions.usedElsewhere")}</span>
          {offered.map((c) => (
            <button
              key={c}
              type="button"
              className="chip st-blue"
              disabled={busy}
              onClick={() => onAdd([c])}
            >
              + {c}
            </button>
          ))}
        </div>
      )}
      {suggestions.byDirection.length > 0 && (
        <label className="field field-inline">
          <span>{t("directions.sameAs")}</span>
          <select
            value=""
            disabled={busy}
            onChange={(e) => {
              const from = suggestions.byDirection.find((d) => d.destinationId === e.target.value);
              if (from) onAdd(from.cities);
            }}
          >
            <option value="" />
            {suggestions.byDirection.map((d) => (
              <option key={d.destinationId} value={d.destinationId}>
                {name.country(countryById.get(d.destinationId))}: {d.cities.join(", ")}
              </option>
            ))}
          </select>
        </label>
      )}
    </>
  );
}

/** Cities of a direction not saved yet: sent along with it. */
function CitiesField({
  value,
  onAdd,
  onRemove,
  suggestions,
}: {
  value: string[];
  onAdd: (cities: string[]) => void;
  onRemove: (city: string) => void;
  suggestions: CitySuggestions;
}) {
  const { t } = useTranslation();
  return (
    <fieldset className="module-block">
      <legend>{t("reference.colCities")}</legend>
      <p className="muted small">{t("directions.citiesHint")}</p>
      {value.length > 0 && (
        <div className="chips">
          {value.map((city) => (
            <span key={city} className="chip st-gray chip-removable">
              {city}
              <button
                type="button"
                aria-label={t("directions.removeCity", { city })}
                onClick={() => onRemove(city)}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <CityInput have={value} suggestions={suggestions} onAdd={onAdd} />
    </fieldset>
  );
}

/** Cities of a saved direction: each change is saved at once. A city a case names is
 * switched off instead of deleted. */
function RuleCities({ rule, suggestions }: { rule: Rule; suggestions: CitySuggestions }) {
  const { t } = useTranslation();
  const refresh = useRefreshRules();
  const [centers, setCenters] = useState<VisaCenter[]>(rule.visa_centers);

  const add = useMutation({
    mutationFn: (cities: string[]) =>
      apiPost<Rule>(`/reference/rules/${rule.id}/visa-centers`, { cities }),
    onSuccess: (saved) => {
      setCenters(saved.visa_centers);
      void refresh();
    },
  });
  const setActive = useMutation({
    mutationFn: ({ center, on }: { center: VisaCenter; on: boolean }) =>
      apiPatch<VisaCenter>(`/reference/visa-centers/${center.id}`, { is_active: on }),
    onSuccess: (updated) => {
      setCenters((list) => list.map((c) => (c.id === updated.id ? updated : c)));
      void refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (center: VisaCenter) => apiDelete<void>(`/reference/visa-centers/${center.id}`),
    onSuccess: (_, center) => {
      setCenters((list) => list.filter((c) => c.id !== center.id));
      void refresh();
    },
    onError: (error, center) => {
      if (errorCode(error) === "visa_center_in_use" && window.confirm(t("directions.cityInUse"))) {
        setActive.mutate({ center, on: false });
      }
    },
  });
  const busy = add.isPending || setActive.isPending || remove.isPending;

  return (
    <fieldset className="module-block">
      <legend>{t("reference.colCities")}</legend>
      <p className="muted small">{t("directions.citiesHint")}</p>
      {centers.length > 0 && (
        <div className="chips">
          {centers.map((c) =>
            c.is_active ? (
              <span key={c.id} className="chip st-gray chip-removable">
                {c.city_ru}
                <button
                  type="button"
                  aria-label={t("directions.removeCity", { city: c.city_ru })}
                  disabled={busy}
                  onClick={() => remove.mutate(c)}
                >
                  ×
                </button>
              </span>
            ) : (
              <span key={c.id} className="chip st-gray chip-removable row-off">
                {t("directions.cityOff", { city: c.city_ru })}
                <button
                  type="button"
                  aria-label={t("directions.switchCityOn", { city: c.city_ru })}
                  disabled={busy}
                  onClick={() => setActive.mutate({ center: c, on: true })}
                >
                  ↺
                </button>
              </span>
            ),
          )}
        </div>
      )}
      <CityInput
        have={centers.map((c) => c.city_ru)}
        suggestions={suggestions}
        onAdd={(cities) => add.mutate(cities)}
        busy={busy}
      />
      {add.isError && <ErrorAlert error={add.error} />}
      {setActive.isError && <ErrorAlert error={setActive.error} />}
      {remove.isError && errorCode(remove.error) !== "visa_center_in_use" && (
        <ErrorAlert error={remove.error} />
      )}
    </fieldset>
  );
}

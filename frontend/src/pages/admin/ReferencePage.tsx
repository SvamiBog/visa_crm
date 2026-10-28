import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { apiGet, apiPatch, apiPost, errorCode } from "../../api";
import { CHECKLISTS_KEY, type ChecklistItem } from "../../checklists";
import { formatDate } from "../../contacts";
import {
  useCountries,
  useLocalName,
  useOperators,
  type Country,
  type Operator,
} from "../../reference";
import {
  ChecklistCountrySelect,
  ChecklistItems,
  ItemForm,
  useChecklistCountry,
} from "../checklists/ChecklistParts";

type Tab = "countries" | "operators" | "checklists";
const TABS: Tab[] = ["countries", "operators", "checklists"];

/** Admin: the shared reference of countries, operators and document checklists. Directions
 * and their cities are each workspace's own (backlog 1.20). */
export function ReferencePage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("countries");
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
      {tab === "checklists" && <ChecklistsTab />}
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

// --- Checklists ----------------------------------------------------------------------

/** The shared document checklist of each country (backlog 2.2): every workspace gets it. */
function ChecklistsTab() {
  const { t } = useTranslation();
  const [countryId, setCountryId] = useChecklistCountry(true);
  const [adding, setAdding] = useState(false);
  const items = useQuery({
    queryKey: [...CHECKLISTS_KEY, "admin", countryId],
    queryFn: () => apiGet<ChecklistItem[]>(`/admin/checklists/${countryId}`),
    enabled: !!countryId,
  });

  return (
    <>
      <div className="toolbar">
        <label className="field field-inline">
          <span>{t("checklists.country")}</span>
          <ChecklistCountrySelect
            admin
            value={countryId}
            onChange={(id) => {
              setCountryId(id);
              setAdding(false);
            }}
          />
        </label>
        {countryId && !adding && (
          <button type="button" className="btn btn-p" onClick={() => setAdding(true)}>
            {t("checklists.add")}
          </button>
        )}
      </div>
      <p className="muted small">{t("checklists.adminHint")}</p>
      {adding && (
        <section className="card">
          <ItemForm admin countryId={countryId} onDone={() => setAdding(false)} />
        </section>
      )}
      <section className="card">
        {!countryId && <p className="muted">{t("checklists.pickCountry")}</p>}
        {countryId && items.isPending && <p className="muted">{t("common.loading")}</p>}
        {items.isError && <ErrorAlert error={items.error} />}
        {countryId && items.data && (
          <ChecklistItems
            admin
            countryId={countryId}
            items={items.data}
            editable
            reorderable
            empty={t("checklists.sharedEmpty")}
          />
        )}
      </section>
    </>
  );
}

// --- Countries -----------------------------------------------------------------------

function CountriesTab() {
  const { t, i18n } = useTranslation();
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
                  <th>{t("reference.schengenSince")}</th>
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
                    <td className="muted nowrap">
                      {formatDate(c.schengen_since, i18n.language) || "—"}
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
  const [schengenSince, setSchengenSince] = useState(country?.schengen_since ?? "");
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
        schengen_since: schengenSince || null,
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
        <label className="field">
          <span>{t("reference.schengenSince")}</span>
          <input
            type="date"
            value={schengenSince}
            onChange={(e) => setSchengenSince(e.target.value)}
          />
        </label>
      </div>
      <p className="muted small">{t("reference.passportHint")}</p>
      <p className="muted small">{t("reference.schengenHint")}</p>
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

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { apiGet, apiPatch, apiPost, apiPut, errorCode } from "../../api";
import { PURPOSE_KINDS, type PurposeKind } from "../../checklists";
import { byId, useCountries, useLocalName, type Country } from "../../reference";
import { rulesSummary, SYSTEM_KINDS, type SystemKind, type VaultSystem } from "../../vault";
import { ReminderSettings } from "./Attention";

// A specialist's own lists (backend/app/api/dictionaries.py).

const BASE = "/dictionaries";
const CURRENCIES = ["BYN", "PLN", "USD", "EUR"] as const;
const TERMS_TYPES = ["percent", "fixed", "none", "unspecified"] as const;
const HALLS = ["standard", "premium"] as const;
const KINDS = ["company", "individual"] as const;

type Entry = {
  id: string;
  name: string;
  sort_order: number;
  is_active: boolean;
  // Purposes only: the platform's kind, which the shared checklists go by.
  kind?: PurposeKind | null;
};
type Terms = {
  country_id: string | null;
  terms_type: (typeof TERMS_TYPES)[number];
  percent: string | null;
  amount: string | null;
  currency: string | null;
};
type Partner = {
  id: string;
  name: string;
  contact: string | null;
  note: string | null;
  is_active: boolean;
  terms: Terms[];
};
type Price = {
  country_id: string;
  purpose_id: string | null;
  hall: (typeof HALLS)[number];
  amount: string;
  currency: string;
};
type Registrar = {
  id: string;
  name: string;
  kind: (typeof KINDS)[number];
  contact: string | null;
  note: string | null;
  is_active: boolean;
  currency: string;
  low_balance: string | null;
  prices: Price[];
};
type Prepayment = { country_id: string; purpose_id: string | null; amount: string };
type Money = {
  base_currency: string;
  payment_currencies: string[];
  rate_source: string;
  tax_rate: string;
};

type Tab =
  | "purposes"
  | "channels"
  | "partners"
  | "registrars"
  | "prepayments"
  | "vaultSystems"
  | "money"
  | "reminders";
const TABS: Tab[] = [
  "purposes",
  "channels",
  "partners",
  "registrars",
  "prepayments",
  "vaultSystems",
  "money",
  "reminders",
];

const key = (name: string) => ["dictionaries", name] as const;

function useList<T>(name: string) {
  return useQuery({ queryKey: key(name), queryFn: () => apiGet<T>(`${BASE}/${name}`) });
}

function useRefresh(name: string) {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: key(name) });
}

/** Amounts are typed with a comma or a dot; the API takes a decimal string. */
function toAmount(value: string): string {
  return value.trim().replace(",", ".");
}

function ErrorAlert({ error }: { error: unknown }) {
  const { t } = useTranslation();
  return (
    <div className="alert alert-error" role="alert">
      {t(`errors.${errorCode(error)}`, { defaultValue: t("errors.unknown") })}
    </div>
  );
}

function StatusChip({ active }: { active: boolean }) {
  const { t } = useTranslation();
  return active ? (
    <span className="chip st-green">{t("reference.on")}</span>
  ) : (
    <span className="chip st-gray">{t("reference.off")}</span>
  );
}

export function DictionariesPage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("purposes");
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("dict.title")}</h1>
          <div className="muted">{t("dict.subtitle")}</div>
        </div>
      </header>
      <div className="tabs" role="tablist">
        {TABS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={tab === name}
            className={tab === name ? "tab active" : "tab"}
            onClick={() => setTab(name)}
          >
            {t(`dict.tab.${name}`)}
          </button>
        ))}
      </div>
      {(tab === "purposes" || tab === "channels") && <SimpleList key={tab} name={tab} />}
      {tab === "partners" && <Partners />}
      {tab === "registrars" && <Registrars />}
      {tab === "prepayments" && <Prepayments />}
      {tab === "vaultSystems" && <VaultSystems />}
      {tab === "money" && <MoneySettings />}
      {tab === "reminders" && <ReminderSettings />}
    </div>
  );
}

// --- Visa purposes and lead channels -------------------------------------------------

function SimpleList({ name }: { name: "purposes" | "channels" }) {
  const { t } = useTranslation();
  const list = useList<Entry[]>(name);
  const refresh = useRefresh(name);
  const [newName, setNewName] = useState("");
  const [newKind, setNewKind] = useState<PurposeKind | "">("");
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  const withKind = name === "purposes";

  const add = useMutation({
    mutationFn: () =>
      apiPost<Entry>(`${BASE}/${name}`, {
        name: newName.trim(),
        // New entries go to the end of the list.
        sort_order: Math.max(-1, ...(list.data ?? []).map((e) => e.sort_order)) + 1,
        ...(withKind ? { kind: newKind || null } : {}),
      }),
    onSuccess: () => {
      setNewName("");
      setNewKind("");
      void refresh();
    },
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<Entry> }) =>
      apiPatch<Entry>(`${BASE}/${name}/${id}`, body),
    onSuccess: () => {
      setRenaming(null);
      void refresh();
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    add.mutate();
  };

  return (
    <>
      <p className="muted small">{t(`dict.${name}Hint`)}</p>
      <section className="card">
        {list.isPending && <p className="muted">{t("common.loading")}</p>}
        {list.data?.length === 0 && <p className="muted">{t("dict.empty")}</p>}
        {list.data && list.data.length > 0 && (
          <ul className="plain-list">
            {list.data.map((entry) => (
              <li key={entry.id} className={entry.is_active ? undefined : "row-off"}>
                {renaming?.id === entry.id ? (
                  <form
                    className="inline-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      update.mutate({ id: entry.id, body: { name: renaming.name.trim() } });
                    }}
                  >
                    <input
                      aria-label={t("dict.name")}
                      autoFocus
                      required
                      maxLength={100}
                      value={renaming.name}
                      onChange={(e) => setRenaming({ id: entry.id, name: e.target.value })}
                    />
                    <button type="submit" className="btn btn-sm btn-p" disabled={update.isPending}>
                      {t("common.save")}
                    </button>
                    <button type="button" className="btn btn-sm" onClick={() => setRenaming(null)}>
                      {t("common.cancel")}
                    </button>
                  </form>
                ) : (
                  <>
                    <span>
                      {entry.name} {!entry.is_active && <StatusChip active={false} />}
                    </span>
                    <span className="actions">
                      {withKind && (
                        <KindSelect
                          value={entry.kind ?? ""}
                          disabled={update.isPending}
                          onChange={(kind) =>
                            update.mutate({ id: entry.id, body: { kind: kind || null } })
                          }
                        />
                      )}
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => setRenaming({ id: entry.id, name: entry.name })}
                      >
                        {t("dict.rename")}
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={update.isPending}
                        onClick={() =>
                          update.mutate({ id: entry.id, body: { is_active: !entry.is_active } })
                        }
                      >
                        {t(entry.is_active ? "reference.switchOff" : "reference.switchOn")}
                      </button>
                    </span>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
        {update.isError && <ErrorAlert error={update.error} />}
      </section>
      <form className="card form-card" onSubmit={submit}>
        <label className="field">
          <span>{t(`dict.new.${name}`)}</span>
          <input
            required
            maxLength={100}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
        </label>
        {withKind && (
          <label className="field">
            <span>{t("dict.purposeKind")}</span>
            <KindSelect value={newKind} onChange={setNewKind} />
            <small className="muted">{t("dict.purposeKindHint")}</small>
          </label>
        )}
        {add.isError && <ErrorAlert error={add.error} />}
        <div className="form-actions">
          <button type="submit" className="btn btn-p" disabled={add.isPending || !newName.trim()}>
            {t("dict.add")}
          </button>
        </div>
      </form>
    </>
  );
}

/** A purpose's kind: the shared checklists (and later the forms) go by it. */
function KindSelect({
  value,
  disabled,
  onChange,
}: {
  value: PurposeKind | "";
  disabled?: boolean;
  onChange: (kind: PurposeKind | "") => void;
}) {
  const { t } = useTranslation();
  return (
    <select
      aria-label={t("dict.purposeKind")}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value as PurposeKind | "")}
    >
      <option value="">{t("dict.purposeKindNone")}</option>
      {PURPOSE_KINDS.map((kind) => (
        <option key={kind} value={kind}>
          {t(`purposeKind.${kind}`)}
        </option>
      ))}
    </select>
  );
}

// --- Vault systems -------------------------------------------------------------------

function VaultSystems() {
  const { t } = useTranslation();
  const list = useList<VaultSystem[]>("vault-systems");
  const [editing, setEditing] = useState<VaultSystem | "new" | null>(null);

  return (
    <>
      <div className="toolbar">
        <p className="muted small">{t("dict.vaultSystemsHint")}</p>
        {editing === null && (
          <button type="button" className="btn btn-p" onClick={() => setEditing("new")}>
            {t("dict.addSystem")}
          </button>
        )}
      </div>
      {editing !== null && (
        <SystemForm
          key={editing === "new" ? "new" : editing.id}
          system={editing === "new" ? undefined : editing}
          nextOrder={Math.max(-1, ...(list.data ?? []).map((s) => s.sort_order)) + 1}
          onClose={() => setEditing(null)}
        />
      )}
      <section className="card">
        {list.isPending && <p className="muted">{t("common.loading")}</p>}
        {list.data?.length === 0 && <p className="muted">{t("dict.empty")}</p>}
        {list.data && list.data.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("dict.name")}</th>
                  <th>{t("dict.passwordRules")}</th>
                  <th>{t("admin.colStatus")}</th>
                  <th aria-label={t("admin.colActions")} />
                </tr>
              </thead>
              <tbody>
                {list.data.map((s) => (
                  <tr key={s.id} className={s.is_active ? undefined : "row-off"}>
                    <td>
                      {s.url ? (
                        <a href={s.url} target="_blank" rel="noreferrer">
                          {s.name}
                        </a>
                      ) : (
                        s.name
                      )}
                      <div className="muted small">{t(`dict.systemKind.${s.kind}`)}</div>
                    </td>
                    <td className="mono">{rulesSummary(s)}</td>
                    <td>
                      <StatusChip active={s.is_active} />
                    </td>
                    <td>
                      <div className="actions">
                        <button type="button" className="btn btn-sm" onClick={() => setEditing(s)}>
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

function SystemForm({
  system,
  nextOrder,
  onClose,
}: {
  system?: VaultSystem;
  nextOrder: number;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const refresh = useRefresh("vault-systems");
  const [name, setName] = useState(system?.name ?? "");
  const [kind, setKind] = useState<SystemKind>(system?.kind ?? "visa_center");
  const [url, setUrl] = useState(system?.url ?? "");
  const [length, setLength] = useState(String(system?.password_length ?? 12));
  const [lower, setLower] = useState(system?.password_lower ?? true);
  const [upper, setUpper] = useState(system?.password_upper ?? true);
  const [digits, setDigits] = useState(system?.password_digits ?? true);
  const [symbols, setSymbols] = useState(system?.password_symbols ?? "!@#$%*?");
  const [active, setActive] = useState(system?.is_active ?? true);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: name.trim(),
        kind,
        url: url.trim() || null,
        password_length: Number(length),
        password_lower: lower,
        password_upper: upper,
        password_digits: digits,
        password_symbols: symbols.replace(/\s/g, ""),
        is_active: active,
      };
      return system
        ? apiPatch<VaultSystem>(`${BASE}/vault-systems/${system.id}`, body)
        : apiPost<VaultSystem>(`${BASE}/vault-systems`, { ...body, sort_order: nextOrder });
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
      <h2 className="h2">{system ? system.name : t("dict.newSystem")}</h2>
      <div className="form-grid">
        <label className="field">
          <span>{t("dict.name")}</span>
          <input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("dict.kindLabel")}</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as SystemKind)}>
            {SYSTEM_KINDS.map((k) => (
              <option key={k} value={k}>
                {t(`dict.systemKind.${k}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("dict.siteUrl")}</span>
          <input
            type="url"
            maxLength={300}
            placeholder="https://"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </label>
      </div>
      <fieldset className="module-block">
        <legend>{t("dict.passwordRules")}</legend>
        <p className="muted small">{t("dict.passwordRulesHint")}</p>
        <div className="form-grid">
          <label className="field">
            <span>{t("dict.passwordLength")}</span>
            <input
              type="number"
              required
              min={6}
              max={64}
              value={length}
              onChange={(e) => setLength(e.target.value)}
            />
          </label>
          <label className="field">
            <span>{t("dict.passwordSymbols")}</span>
            <input
              className="mono"
              maxLength={32}
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
            />
            <small className="muted">{t("dict.passwordSymbolsHint")}</small>
          </label>
        </div>
        <label className="checkbox">
          <input type="checkbox" checked={lower} onChange={(e) => setLower(e.target.checked)} />
          <span>{t("dict.passwordLower")}</span>
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={upper} onChange={(e) => setUpper(e.target.checked)} />
          <span>{t("dict.passwordUpper")}</span>
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={digits} onChange={(e) => setDigits(e.target.checked)} />
          <span>{t("dict.passwordDigits")}</span>
        </label>
      </fieldset>
      <label className="checkbox">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        <span>{t("reference.activeLabel")}</span>
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

// --- Shared selects ------------------------------------------------------------------

function useSortedCountries() {
  const name = useLocalName();
  const countries = useCountries();
  return useMemo(
    () =>
      [...(countries.data ?? [])].sort((a, b) => name.country(a).localeCompare(name.country(b))),
    [countries.data, name],
  );
}

function CountrySelect({
  value,
  onChange,
  countries,
  anyLabel,
  label,
}: {
  value: string | null;
  onChange: (id: string | null) => void;
  countries: Country[];
  anyLabel?: string;
  label: string;
}) {
  const name = useLocalName();
  return (
    <select
      aria-label={label}
      required={!anyLabel}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
    >
      <option value="">{anyLabel ?? ""}</option>
      {countries.map((c) => (
        <option key={c.id} value={c.id}>
          {name.country(c)}
        </option>
      ))}
    </select>
  );
}

function PurposeSelect({
  value,
  onChange,
  purposes,
}: {
  value: string | null;
  onChange: (id: string | null) => void;
  purposes: Entry[];
}) {
  const { t } = useTranslation();
  return (
    <select
      aria-label={t("dict.purpose")}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
    >
      <option value="">{t("dict.anyPurpose")}</option>
      {purposes
        .filter((p) => p.is_active || p.id === value)
        .map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
    </select>
  );
}

// --- Partners ------------------------------------------------------------------------

function TermsSummary({ terms }: { terms: Terms[] }) {
  const { t } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  if (terms.length === 0) return <span className="muted">{t("dict.noTerms")}</span>;
  return (
    <>
      {terms.map((term) => (
        <div key={term.country_id ?? "default"} className="small">
          <span className="muted">
            {term.country_id
              ? name.country(countryById.get(term.country_id))
              : t("dict.allCountries")}
            :{" "}
          </span>
          <TermsValue terms={term} />
        </div>
      ))}
    </>
  );
}

function TermsValue({ terms }: { terms: Terms }) {
  const { t } = useTranslation();
  if (terms.terms_type === "percent") return <>{t("dict.percentOf", { percent: terms.percent })}</>;
  if (terms.terms_type === "fixed") return <>{`${terms.amount} ${terms.currency}`}</>;
  return <>{t(`dict.terms.${terms.terms_type}`)}</>;
}

function Partners() {
  const { t } = useTranslation();
  const list = useList<Partner[]>("partners");
  const [editing, setEditing] = useState<Partner | "new" | null>(null);
  return (
    <>
      <div className="toolbar">
        <p className="muted small">{t("dict.partnersHint")}</p>
        {editing === null && (
          <button type="button" className="btn btn-p" onClick={() => setEditing("new")}>
            {t("dict.addPartner")}
          </button>
        )}
      </div>
      {editing !== null && (
        <PartnerForm
          key={editing === "new" ? "new" : editing.id}
          partner={editing === "new" ? undefined : editing}
          onClose={() => setEditing(null)}
        />
      )}
      <section className="card">
        {list.isPending && <p className="muted">{t("common.loading")}</p>}
        {list.data?.length === 0 && <p className="muted">{t("dict.empty")}</p>}
        {list.data && list.data.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("dict.name")}</th>
                  <th>{t("dict.contact")}</th>
                  <th>{t("dict.terms.title")}</th>
                  <th>{t("admin.colStatus")}</th>
                  <th aria-label={t("admin.colActions")} />
                </tr>
              </thead>
              <tbody>
                {list.data.map((p) => (
                  <tr key={p.id} className={p.is_active ? undefined : "row-off"}>
                    <td>
                      {p.name}
                      {p.note && <div className="muted small">{p.note}</div>}
                    </td>
                    <td className="muted">{p.contact ?? "—"}</td>
                    <td>
                      <TermsSummary terms={p.terms} />
                    </td>
                    <td>
                      <StatusChip active={p.is_active} />
                    </td>
                    <td>
                      <div className="actions">
                        <button type="button" className="btn btn-sm" onClick={() => setEditing(p)}>
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

function PartnerForm({ partner, onClose }: { partner?: Partner; onClose: () => void }) {
  const { t } = useTranslation();
  const refresh = useRefresh("partners");
  const countries = useSortedCountries();
  const [name, setName] = useState(partner?.name ?? "");
  const [contact, setContact] = useState(partner?.contact ?? "");
  const [note, setNote] = useState(partner?.note ?? "");
  const [active, setActive] = useState(partner?.is_active ?? true);
  const [terms, setTerms] = useState<Terms[]>(
    partner?.terms ?? [
      { country_id: null, terms_type: "percent", percent: "", amount: null, currency: null },
    ],
  );

  const setRow = (index: number, patch: Partial<Terms>) =>
    setTerms((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const save = useMutation({
    mutationFn: async () => {
      const body = {
        name: name.trim(),
        contact: contact.trim() || null,
        note: note.trim() || null,
        is_active: active,
      };
      const saved = partner
        ? await apiPatch<Partner>(`${BASE}/partners/${partner.id}`, body)
        : await apiPost<Partner>(`${BASE}/partners`, body);
      return apiPut<Partner>(
        `${BASE}/partners/${saved.id}/terms`,
        terms.map((row) => ({
          country_id: row.country_id,
          terms_type: row.terms_type,
          percent: row.terms_type === "percent" ? toAmount(row.percent ?? "") : null,
          amount: row.terms_type === "fixed" ? toAmount(row.amount ?? "") : null,
          currency: row.terms_type === "fixed" ? (row.currency ?? "BYN") : null,
        })),
      );
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
      <h2 className="h2">{partner ? partner.name : t("dict.newPartner")}</h2>
      <div className="form-grid">
        <label className="field">
          <span>{t("dict.name")}</span>
          <input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("dict.contact")}</span>
          <input maxLength={200} value={contact} onChange={(e) => setContact(e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span>{t("reference.notes")}</span>
        <input maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>

      <fieldset className="module-block">
        <legend>{t("dict.terms.title")}</legend>
        <p className="muted small">{t("dict.termsHint")}</p>
        <div className="rows-editor">
          {terms.map((row, index) => (
            <div key={index} className="row-editor">
              <CountrySelect
                label={t("dict.country")}
                value={row.country_id}
                onChange={(id) => setRow(index, { country_id: id })}
                countries={countries}
                anyLabel={t("dict.allCountries")}
              />
              <select
                aria-label={t("dict.terms.title")}
                value={row.terms_type}
                onChange={(e) => setRow(index, { terms_type: e.target.value as Terms["terms_type"] })}
              >
                {TERMS_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {t(`dict.terms.${type}`)}
                  </option>
                ))}
              </select>
              {row.terms_type === "percent" && (
                <input
                  aria-label={t("dict.percent")}
                  inputMode="decimal"
                  required
                  placeholder="%"
                  value={row.percent ?? ""}
                  onChange={(e) => setRow(index, { percent: e.target.value })}
                />
              )}
              {row.terms_type === "fixed" && (
                <>
                  <input
                    aria-label={t("dict.amount")}
                    inputMode="decimal"
                    required
                    value={row.amount ?? ""}
                    onChange={(e) => setRow(index, { amount: e.target.value })}
                  />
                  <select
                    aria-label={t("dict.currency")}
                    value={row.currency ?? "BYN"}
                    onChange={(e) => setRow(index, { currency: e.target.value })}
                  >
                    {CURRENCIES.map((c) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                </>
              )}
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setTerms((rows) => rows.filter((_, i) => i !== index))}
              >
                {t("dict.remove")}
              </button>
            </div>
          ))}
        </div>
        <div className="form-actions">
          <button
            type="button"
            className="btn btn-sm"
            onClick={() =>
              setTerms((rows) => [
                ...rows,
                { country_id: null, terms_type: "percent", percent: "", amount: null, currency: null },
              ])
            }
          >
            {t("dict.addTerms")}
          </button>
        </div>
      </fieldset>

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

// --- Registrars ----------------------------------------------------------------------

function Registrars() {
  const { t } = useTranslation();
  const list = useList<Registrar[]>("registrars");
  const purposes = useList<Entry[]>("purposes");
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const purposeById = useMemo(() => byId(purposes.data), [purposes.data]);
  const [editing, setEditing] = useState<Registrar | "new" | null>(null);

  return (
    <>
      <div className="toolbar">
        <p className="muted small">{t("dict.registrarsHint")}</p>
        {editing === null && (
          <button type="button" className="btn btn-p" onClick={() => setEditing("new")}>
            {t("dict.addRegistrar")}
          </button>
        )}
      </div>
      {editing !== null && (
        <RegistrarForm
          key={editing === "new" ? "new" : editing.id}
          registrar={editing === "new" ? undefined : editing}
          purposes={purposes.data ?? []}
          onClose={() => setEditing(null)}
        />
      )}
      <section className="card">
        {list.isPending && <p className="muted">{t("common.loading")}</p>}
        {list.data?.length === 0 && <p className="muted">{t("dict.empty")}</p>}
        {list.data && list.data.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("dict.name")}</th>
                  <th>{t("dict.contact")}</th>
                  <th>{t("dict.prices")}</th>
                  <th>{t("admin.colStatus")}</th>
                  <th aria-label={t("admin.colActions")} />
                </tr>
              </thead>
              <tbody>
                {list.data.map((r) => (
                  <tr key={r.id} className={r.is_active ? undefined : "row-off"}>
                    <td>
                      {r.name}
                      <div className="muted small">
                        {t(`dict.kind.${r.kind}`)} · {t("dict.settlesIn", { currency: r.currency })}
                      </div>
                    </td>
                    <td className="muted">{r.contact ?? "—"}</td>
                    <td>
                      {r.prices.length === 0 && <span className="muted">{t("dict.noPrices")}</span>}
                      {r.prices.map((p, i) => (
                        <div key={i} className="small">
                          <span className="muted">
                            {name.country(countryById.get(p.country_id))} ·{" "}
                            {p.purpose_id
                              ? purposeById.get(p.purpose_id)?.name
                              : t("dict.anyPurpose")}{" "}
                            · {t(`dict.hall.${p.hall}`)}:{" "}
                          </span>
                          <span className="mono">
                            {p.amount} {p.currency}
                          </span>
                        </div>
                      ))}
                    </td>
                    <td>
                      <StatusChip active={r.is_active} />
                    </td>
                    <td>
                      <div className="actions">
                        <button type="button" className="btn btn-sm" onClick={() => setEditing(r)}>
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

function RegistrarForm({
  registrar,
  purposes,
  onClose,
}: {
  registrar?: Registrar;
  purposes: Entry[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const refresh = useRefresh("registrars");
  const countries = useSortedCountries();
  const [name, setName] = useState(registrar?.name ?? "");
  const [kind, setKind] = useState<Registrar["kind"]>(registrar?.kind ?? "company");
  const [contact, setContact] = useState(registrar?.contact ?? "");
  const [note, setNote] = useState(registrar?.note ?? "");
  const [active, setActive] = useState(registrar?.is_active ?? true);
  const [prices, setPrices] = useState<Price[]>(registrar?.prices ?? []);
  const money = useList<Money>("money");
  // A new registrar settles in the base currency until another is chosen.
  const [chosenCurrency, setCurrency] = useState<string | null>(registrar?.currency ?? null);
  const currency = chosenCurrency ?? money.data?.base_currency ?? CURRENCIES[0];
  const [lowBalance, setLowBalance] = useState(registrar?.low_balance ?? "");

  const setRow = (index: number, patch: Partial<Price>) =>
    setPrices((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const save = useMutation({
    mutationFn: async () => {
      const body = {
        name: name.trim(),
        kind,
        contact: contact.trim() || null,
        note: note.trim() || null,
        is_active: active,
        currency,
        low_balance: lowBalance.trim() ? toAmount(lowBalance) : null,
      };
      const saved = registrar
        ? await apiPatch<Registrar>(`${BASE}/registrars/${registrar.id}`, body)
        : await apiPost<Registrar>(`${BASE}/registrars`, body);
      return apiPut<Registrar>(
        `${BASE}/registrars/${saved.id}/prices`,
        prices.map((p) => ({ ...p, amount: toAmount(p.amount) })),
      );
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
      <h2 className="h2">{registrar ? registrar.name : t("dict.newRegistrar")}</h2>
      <div className="form-grid">
        <label className="field">
          <span>{t("dict.name")}</span>
          <input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("dict.kindLabel")}</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as Registrar["kind"])}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {t(`dict.kind.${k}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("dict.contact")}</span>
          <input maxLength={200} value={contact} onChange={(e) => setContact(e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span>{t("reference.notes")}</span>
        <input maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>

      <fieldset className="module-block">
        <legend>{t("dict.settlements")}</legend>
        <p className="muted small">{t("dict.settlementsHint")}</p>
        <div className="form-grid">
          <label className="field">
            <span>{t("dict.settlementCurrency")}</span>
            <select value={currency} onChange={(e) => setCurrency(e.target.value)}>
              {CURRENCIES.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{t("dict.lowBalance", { currency })}</span>
            <input
              inputMode="decimal"
              pattern="[0-9 ]*([.,][0-9]{1,2})?"
              value={lowBalance}
              onChange={(e) => setLowBalance(e.target.value)}
            />
          </label>
        </div>
      </fieldset>

      <fieldset className="module-block">
        <legend>{t("dict.prices")}</legend>
        <p className="muted small">{t("dict.pricesHint")}</p>
        <div className="rows-editor">
          {prices.map((row, index) => (
            <div key={index} className="row-editor">
              <CountrySelect
                label={t("dict.country")}
                value={row.country_id}
                onChange={(id) => setRow(index, { country_id: id ?? "" })}
                countries={countries}
              />
              <PurposeSelect
                value={row.purpose_id}
                onChange={(id) => setRow(index, { purpose_id: id })}
                purposes={purposes}
              />
              <select
                aria-label={t("dict.hallLabel")}
                value={row.hall}
                onChange={(e) => setRow(index, { hall: e.target.value as Price["hall"] })}
              >
                {HALLS.map((h) => (
                  <option key={h} value={h}>
                    {t(`dict.hall.${h}`)}
                  </option>
                ))}
              </select>
              <input
                aria-label={t("dict.amount")}
                inputMode="decimal"
                required
                value={row.amount}
                onChange={(e) => setRow(index, { amount: e.target.value })}
              />
              <select
                aria-label={t("dict.currency")}
                value={row.currency}
                onChange={(e) => setRow(index, { currency: e.target.value })}
              >
                {CURRENCIES.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setPrices((rows) => rows.filter((_, i) => i !== index))}
              >
                {t("dict.remove")}
              </button>
            </div>
          ))}
        </div>
        <div className="form-actions">
          <button
            type="button"
            className="btn btn-sm"
            onClick={() =>
              setPrices((rows) => [
                ...rows,
                { country_id: "", purpose_id: null, hall: "standard", amount: "", currency: "USD" },
              ])
            }
          >
            {t("dict.addPrice")}
          </button>
        </div>
      </fieldset>

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

// --- Prepayments ---------------------------------------------------------------------

function Prepayments() {
  const { t } = useTranslation();
  const list = useList<Prepayment[]>("prepayments");
  const purposes = useList<Entry[]>("purposes");
  const money = useList<Money>("money");
  if (list.isPending || purposes.isPending) return <p className="muted">{t("common.loading")}</p>;
  return (
    <PrepaymentsForm
      initial={list.data ?? []}
      purposes={purposes.data ?? []}
      currency={money.data?.base_currency ?? ""}
    />
  );
}

function PrepaymentsForm({
  initial,
  purposes,
  currency,
}: {
  initial: Prepayment[];
  purposes: Entry[];
  currency: string;
}) {
  const { t } = useTranslation();
  const refresh = useRefresh("prepayments");
  const countries = useSortedCountries();
  const [rows, setRows] = useState<Prepayment[]>(initial);

  const setRow = (index: number, patch: Partial<Prepayment>) =>
    setRows((list) => list.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const save = useMutation({
    mutationFn: () =>
      apiPut<Prepayment[]>(
        `${BASE}/prepayments`,
        rows.map((r) => ({ ...r, amount: toAmount(r.amount) })),
      ),
    onSuccess: () => void refresh(),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form className="card form-card" onSubmit={submit}>
      <p className="muted small">{t("dict.prepaymentsHint", { currency })}</p>
      {rows.length === 0 && <p className="muted">{t("dict.empty")}</p>}
      <div className="rows-editor">
        {rows.map((row, index) => (
          <div key={index} className="row-editor">
            <CountrySelect
              label={t("dict.country")}
              value={row.country_id}
              onChange={(id) => setRow(index, { country_id: id ?? "" })}
              countries={countries}
            />
            <PurposeSelect
              value={row.purpose_id}
              onChange={(id) => setRow(index, { purpose_id: id })}
              purposes={purposes}
            />
            <input
              aria-label={t("dict.amount")}
              inputMode="decimal"
              required
              value={row.amount}
              onChange={(e) => setRow(index, { amount: e.target.value })}
            />
            <span className="muted">{currency}</span>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setRows((list) => list.filter((_, i) => i !== index))}
            >
              {t("dict.remove")}
            </button>
          </div>
        ))}
      </div>
      {save.isError && <ErrorAlert error={save.error} />}
      {save.isSuccess && (
        <div className="alert alert-ok" role="status">
          {t("dict.saved")}
        </div>
      )}
      <div className="form-actions">
        <button
          type="button"
          className="btn"
          onClick={() =>
            setRows((list) => [...list, { country_id: "", purpose_id: null, amount: "" }])
          }
        >
          {t("dict.addPrepayment")}
        </button>
        <button type="submit" className="btn btn-p" disabled={save.isPending}>
          {t("common.save")}
        </button>
      </div>
    </form>
  );
}

// --- Currencies and tax --------------------------------------------------------------

function MoneySettings() {
  const { t } = useTranslation();
  const money = useList<Money>("money");
  if (!money.data) return <p className="muted">{t("common.loading")}</p>;
  return <MoneyForm money={money.data} />;
}

function MoneyForm({ money }: { money: Money }) {
  const { t } = useTranslation();
  const refresh = useRefresh("money");
  const [chosen, setChosen] = useState(money.payment_currencies);

  const save = useMutation({
    mutationFn: () => apiPatch<Money>(`${BASE}/money`, { payment_currencies: chosen }),
    onSuccess: () => void refresh(),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form className="card form-card" onSubmit={submit}>
      <dl className="facts">
        <dt>{t("cabinet.baseCurrency")}</dt>
        <dd>{money.base_currency}</dd>
        <dt>{t("dict.taxRate")}</dt>
        <dd>{t("dict.taxValue", { rate: money.tax_rate })}</dd>
        <dt>{t("dict.rateSource")}</dt>
        <dd>{t(`dict.source.${money.rate_source}`)}</dd>
      </dl>
      <p className="muted small">{t("dict.moneyAdminHint")}</p>
      <fieldset className="module-block">
        <legend>{t("dict.paymentCurrencies")}</legend>
        {CURRENCIES.map((c) => (
          <label key={c} className="checkbox">
            <input
              type="checkbox"
              checked={c === money.base_currency || chosen.includes(c)}
              disabled={c === money.base_currency}
              onChange={(e) =>
                setChosen((list) => (e.target.checked ? [...list, c] : list.filter((x) => x !== c)))
              }
            />
            {c}
          </label>
        ))}
      </fieldset>
      {save.isError && <ErrorAlert error={save.error} />}
      {save.isSuccess && (
        <div className="alert alert-ok" role="status">
          {t("dict.saved")}
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

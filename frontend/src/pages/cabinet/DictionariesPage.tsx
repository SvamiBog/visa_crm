import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiDelete, apiGet, apiPatch, apiPost, apiPut, errorCode } from "../../api";
import { PURPOSE_KINDS, type PurposeKind } from "../../checklists";
import { useConfirm } from "../../components/ConfirmDialog";
import { Field, invalidProps, RowError, useFieldErrors } from "../../components/Field";
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
/** In the currency it was entered in (pilot T8): a new base currency leaves it as is. */
type Prepayment = {
  country_id: string;
  purpose_id: string | null;
  amount: string;
  currency: string;
};
type Money = {
  base_currency: string;
  payment_currencies: string[];
  rate_source: string;
  /** Null: not set yet; "0.00": no tax. */
  tax_rate: string | null;
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

// An entry's name repeats another one's.
const NAME_CODES = { name_taken: "name" };

/** The fields of the rows of a list sent as a whole: the API names a row by its index. */
function rowFields(count: number, names: string[]): string[] {
  return Array.from({ length: count }, (_, i) => names.map((n) => `${i}.${n}`)).flat();
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
  const addErrors = useFieldErrors(add.error, { codes: NAME_CODES, fields: ["name"] });
  // A rename shows its error under the row; switching off or a kind, in the alert.
  const renameErrors = useFieldErrors(renaming ? update.error : null, {
    codes: NAME_CODES,
    fields: ["name"],
  });
  const renameMessage = useId();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    add.mutate();
  };

  // The form of a new entry is drawn once the list has come: the list arriving above it
  // pushed it down.
  if (list.isPending) {
    return (
      <>
        <p className="muted small">{t(`dict.${name}Hint`)}</p>
        <section className="card">
          <p className="muted">{t("common.loading")}</p>
        </section>
      </>
    );
  }

  return (
    <>
      <p className="muted small">{t(`dict.${name}Hint`)}</p>
      <section className="card">
        {list.data?.length === 0 && <p className="muted">{t("dict.empty")}</p>}
        {list.data && list.data.length > 0 && (
          <ul className="plain-list">
            {list.data.map((entry) => (
              <li key={entry.id} className={entry.is_active ? undefined : "row-off"}>
                {renaming?.id === entry.id ? (
                  <form
                    ref={renameErrors.form}
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
                      {...invalidProps(renameMessage, renameErrors.at("name"))}
                    />
                    <button type="submit" className="btn btn-sm btn-p" disabled={update.isPending}>
                      {t("common.save")}
                    </button>
                    <button type="button" className="btn btn-sm" onClick={() => setRenaming(null)}>
                      {t("common.cancel")}
                    </button>
                    <RowError id={renameMessage} error={renameErrors.at("name")} />
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
        {update.isError && (renaming ? renameErrors.general : update.error) ? (
          <ErrorAlert error={renaming ? renameErrors.general : update.error} />
        ) : null}
      </section>
      <form ref={addErrors.form} className="card form-card" onSubmit={submit}>
        <Field label={t(`dict.new.${name}`)} error={addErrors.at("name")}>
          {(control) => (
            <input
              {...control}
              required
              maxLength={100}
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
          )}
        </Field>
        {withKind && (
          <label className="field">
            <span>{t("dict.purposeKind")}</span>
            <KindSelect value={newKind} onChange={setNewKind} />
            <small className="muted">{t("dict.purposeKindHint")}</small>
          </label>
        )}
        {addErrors.general ? <ErrorAlert error={addErrors.general} /> : null}
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
  const errors = useFieldErrors(save.error, {
    codes: { ...NAME_CODES, password_rules_empty: "password_symbols" },
    fields: ["name", "url", "password_length", "password_symbols"],
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form ref={errors.form} className="card form-card" onSubmit={submit}>
      <h2 className="h2">{system ? system.name : t("dict.newSystem")}</h2>
      <div className="form-grid">
        <Field label={t("dict.name")} error={errors.at("name")}>
          {(control) => (
            <input
              {...control}
              required
              maxLength={100}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>
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
        <Field label={t("dict.siteUrl")} error={errors.at("url")}>
          {(control) => (
            <input
              {...control}
              type="url"
              maxLength={300}
              placeholder="https://"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          )}
        </Field>
      </div>
      <fieldset className="module-block">
        <legend>{t("dict.passwordRules")}</legend>
        <p className="muted small">{t("dict.passwordRulesHint")}</p>
        <div className="form-grid">
          <Field label={t("dict.passwordLength")} error={errors.at("password_length")}>
            {(control) => (
              <input
                {...control}
                type="number"
                required
                min={6}
                max={64}
                value={length}
                onChange={(e) => setLength(e.target.value)}
              />
            )}
          </Field>
          <Field
            label={t("dict.passwordSymbols")}
            hint={t("dict.passwordSymbolsHint")}
            error={errors.at("password_symbols")}
          >
            {(control) => (
              <input
                {...control}
                className="mono"
                maxLength={32}
                value={symbols}
                onChange={(e) => setSymbols(e.target.value)}
              />
            )}
          </Field>
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
      {errors.general ? <ErrorAlert error={errors.general} /> : null}
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
  // A partner deleted or switched off from the list closes its own open form.
  const deletion = useDeletePartner((p) => {
    if (editing !== null && editing !== "new" && editing.id === p.id) setEditing(null);
  });
  // The form deletes on its own: an old refusal from the list must not stay on screen.
  const edit = (value: Partner | "new") => {
    deletion.reset();
    setEditing(value);
  };
  return (
    <>
      <div className="toolbar">
        <p className="muted small">{t("dict.partnersHint")}</p>
        {editing === null && (
          <button type="button" className="btn btn-p" onClick={() => edit("new")}>
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
        {deletion.error && <ErrorAlert error={deletion.error} />}
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
                      <div className="actions actions-row">
                        <button type="button" className="btn btn-sm" onClick={() => edit(p)}>
                          {t("reference.edit")}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          disabled={deletion.pending}
                          aria-label={t("dict.deletePartnerFor", { name: p.name })}
                          onClick={() => deletion.run(p)}
                        >
                          {t("dict.deletePartner")}
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

/** Deleting a partner: one no client card names is removed with its terms; otherwise she is
 * offered to switch it off instead. Shared by the row of the list and the form of a partner. */
function useDeletePartner(onDone: (partner: Partner) => void) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const refresh = useRefresh("partners");
  const done = (partner: Partner) => {
    void refresh();
    onDone(partner);
  };
  const remove = useMutation({
    mutationFn: (partner: Partner) => apiDelete<void>(`${BASE}/partners/${partner.id}`),
    onSuccess: (_, partner) => done(partner),
  });
  const switchOff = useMutation({
    mutationFn: (partner: Partner) =>
      apiPatch<Partner>(`${BASE}/partners/${partner.id}`, { is_active: false }),
    onSuccess: (_, partner) => done(partner),
  });

  const run = async (partner: Partner) => {
    const message = t("dict.confirmDeletePartner", { name: partner.name });
    if (!(await confirm({ message, action: t("confirm.delete") }))) return;
    remove.mutate(partner, {
      onError: async (error) => {
        if (errorCode(error) !== "partner_in_use" || !partner.is_active) return;
        const yes = await confirm({
          message: t("dict.partnerInUseSwitchOff"),
          action: t("confirm.switchOff"),
        });
        if (yes) switchOff.mutate(partner);
      },
    });
  };
  // «In use» for an active partner is not an error: it leads to the offer to switch it off.
  // One that is already off can only stay as it is, so she is told why.
  const offered = errorCode(remove.error) === "partner_in_use" && remove.variables?.is_active;
  const error =
    (remove.isError && !offered ? remove.error : null) ??
    (switchOff.isError ? switchOff.error : null);
  const reset = () => {
    remove.reset();
    switchOff.reset();
  };
  return { run, reset, pending: remove.isPending || switchOff.isPending, error };
}

function PartnerForm({ partner, onClose }: { partner?: Partner; onClose: () => void }) {
  const { t } = useTranslation();
  const refresh = useRefresh("partners");
  const deletion = useDeletePartner(() => onClose());
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
  // The card first, then its terms as a list: a row's mistake is named by its index.
  const errors = useFieldErrors(save.error, {
    fields: ["name", "contact", "note", ...rowFields(terms.length, ["percent", "amount"])],
  });
  const rowMessage = useId();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form ref={errors.form} className="card form-card" onSubmit={submit}>
      <h2 className="h2">{partner ? partner.name : t("dict.newPartner")}</h2>
      <div className="form-grid">
        <Field label={t("dict.name")} error={errors.at("name")}>
          {(control) => (
            <input
              {...control}
              required
              maxLength={200}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>
        <Field label={t("dict.contact")} error={errors.at("contact")}>
          {(control) => (
            <input
              {...control}
              maxLength={200}
              value={contact}
              onChange={(e) => setContact(e.target.value)}
            />
          )}
        </Field>
      </div>
      <Field label={t("reference.notes")} error={errors.at("note")}>
        {(control) => (
          <input
            {...control}
            maxLength={1000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        )}
      </Field>

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
                  {...invalidProps(`${rowMessage}-${index}`, errors.at(`${index}.percent`))}
                />
              )}
              {row.terms_type === "fixed" && (
                <span className="row-editor-pair">
                  <input
                    aria-label={t("dict.amount")}
                    inputMode="decimal"
                    required
                    value={row.amount ?? ""}
                    onChange={(e) => setRow(index, { amount: e.target.value })}
                    {...invalidProps(`${rowMessage}-${index}`, errors.at(`${index}.amount`))}
                  />
                  <select
                    className="select-narrow"
                    aria-label={t("dict.currency")}
                    value={row.currency ?? "BYN"}
                    onChange={(e) => setRow(index, { currency: e.target.value })}
                  >
                    {CURRENCIES.map((c) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                </span>
              )}
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setTerms((rows) => rows.filter((_, i) => i !== index))}
              >
                {t("dict.remove")}
              </button>
              <RowError
                id={`${rowMessage}-${index}`}
                error={errors.at(`${index}.percent`) ?? errors.at(`${index}.amount`)}
              />
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
      {errors.general ? <ErrorAlert error={errors.general} /> : null}
      {deletion.error && <ErrorAlert error={deletion.error} />}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={save.isPending}>
          {t("common.save")}
        </button>
        <button type="button" className="btn" onClick={onClose}>
          {t("common.cancel")}
        </button>
        {partner && (
          <button
            type="button"
            className="btn btn-danger"
            disabled={deletion.pending}
            onClick={() => deletion.run(partner)}
          >
            {t("dict.deletePartner")}
          </button>
        )}
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
  // A registrar deleted or switched off from the list closes its own open form.
  const deletion = useDeleteRegistrar((r) => {
    if (editing !== null && editing !== "new" && editing.id === r.id) setEditing(null);
  });
  // The form deletes on its own: an old refusal from the list must not stay on screen.
  const edit = (value: Registrar | "new") => {
    deletion.reset();
    setEditing(value);
  };

  return (
    <>
      <div className="toolbar">
        <p className="muted small">{t("dict.registrarsHint")}</p>
        {editing === null && (
          <button type="button" className="btn btn-p" onClick={() => edit("new")}>
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
        {deletion.error && <ErrorAlert error={deletion.error} />}
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
                      <div className="actions actions-row">
                        <button type="button" className="btn btn-sm" onClick={() => edit(r)}>
                          {t("reference.edit")}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          disabled={deletion.pending}
                          aria-label={t("dict.deleteRegistrarFor", { name: r.name })}
                          onClick={() => deletion.run(r)}
                        >
                          {t("dict.deleteRegistrar")}
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

/** Deleting a registrar: one no case names and no payment went to is removed with its price
 * list; otherwise she is offered to switch it off instead. Shared by the row of the list and
 * the form of a registrar. */
function useDeleteRegistrar(onDone: (registrar: Registrar) => void) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const done = (registrar: Registrar) => {
    void queryClient.invalidateQueries({ queryKey: key("registrars") });
    // The settlements page lists registrars too.
    void queryClient.invalidateQueries({ queryKey: ["registrars", "ledgers"] });
    onDone(registrar);
  };
  const remove = useMutation({
    mutationFn: (registrar: Registrar) => apiDelete<void>(`${BASE}/registrars/${registrar.id}`),
    onSuccess: (_, registrar) => done(registrar),
  });
  const switchOff = useMutation({
    mutationFn: (registrar: Registrar) =>
      apiPatch<Registrar>(`${BASE}/registrars/${registrar.id}`, { is_active: false }),
    onSuccess: (_, registrar) => done(registrar),
  });

  const run = async (registrar: Registrar) => {
    const message = t("dict.confirmDeleteRegistrar", { name: registrar.name });
    if (!(await confirm({ message, action: t("confirm.delete") }))) return;
    remove.mutate(registrar, {
      onError: async (error) => {
        if (errorCode(error) !== "registrar_in_use" || !registrar.is_active) return;
        const yes = await confirm({
          message: t("dict.registrarInUseSwitchOff"),
          action: t("confirm.switchOff"),
        });
        if (yes) switchOff.mutate(registrar);
      },
    });
  };
  // «In use» for an active registrar is not an error: it leads to the offer to switch it
  // off. One that is already off can only stay as it is, so she is told why.
  const offered = errorCode(remove.error) === "registrar_in_use" && remove.variables?.is_active;
  const error =
    (remove.isError && !offered ? remove.error : null) ??
    (switchOff.isError ? switchOff.error : null);
  const reset = () => {
    remove.reset();
    switchOff.reset();
  };
  return { run, reset, pending: remove.isPending || switchOff.isPending, error };
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
  const deletion = useDeleteRegistrar(() => onClose());

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
  const errors = useFieldErrors(save.error, {
    fields: ["name", "contact", "note", "low_balance", ...rowFields(prices.length, ["amount"])],
  });
  const rowMessage = useId();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form ref={errors.form} className="card form-card" onSubmit={submit}>
      <h2 className="h2">{registrar ? registrar.name : t("dict.newRegistrar")}</h2>
      <div className="form-grid">
        <Field label={t("dict.name")} error={errors.at("name")}>
          {(control) => (
            <input
              {...control}
              required
              maxLength={200}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>
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
        <Field label={t("dict.contact")} error={errors.at("contact")}>
          {(control) => (
            <input
              {...control}
              maxLength={200}
              value={contact}
              onChange={(e) => setContact(e.target.value)}
            />
          )}
        </Field>
      </div>
      <Field label={t("reference.notes")} error={errors.at("note")}>
        {(control) => (
          <input
            {...control}
            maxLength={1000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        )}
      </Field>

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
          <Field label={t("dict.lowBalance", { currency })} error={errors.at("low_balance")}>
            {(control) => (
              <input
                {...control}
                inputMode="decimal"
                pattern="[0-9 ]*([.,][0-9]{1,2})?"
                value={lowBalance}
                onChange={(e) => setLowBalance(e.target.value)}
              />
            )}
          </Field>
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
                {...invalidProps(`${rowMessage}-${index}`, errors.at(`${index}.amount`))}
              />
              <select
                className="select-narrow"
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
              <RowError id={`${rowMessage}-${index}`} error={errors.at(`${index}.amount`)} />
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
      {errors.general ? <ErrorAlert error={errors.general} /> : null}
      {deletion.error && <ErrorAlert error={deletion.error} />}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={save.isPending}>
          {t("common.save")}
        </button>
        <button type="button" className="btn" onClick={onClose}>
          {t("common.cancel")}
        </button>
        {registrar && (
          <button
            type="button"
            className="btn btn-danger"
            disabled={deletion.pending}
            onClick={() => deletion.run(registrar)}
          >
            {t("dict.deleteRegistrar")}
          </button>
        )}
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
  if (list.isPending || purposes.isPending || money.isPending) {
    return <p className="muted">{t("common.loading")}</p>;
  }
  return (
    <PrepaymentsForm
      initial={list.data ?? []}
      purposes={purposes.data ?? []}
      base={money.data?.base_currency ?? CURRENCIES[0]}
      currencies={money.data?.payment_currencies ?? [...CURRENCIES]}
    />
  );
}

function PrepaymentsForm({
  initial,
  purposes,
  base,
  currencies,
}: {
  initial: Prepayment[];
  purposes: Entry[];
  /** A new row starts in the base currency; the others are the ones she accepts. */
  base: string;
  currencies: string[];
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
  const errors = useFieldErrors(save.error, { fields: rowFields(rows.length, ["amount"]) });
  const rowMessage = useId();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form ref={errors.form} className="card form-card" onSubmit={submit}>
      <p className="muted small">{t("dict.prepaymentsHint")}</p>
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
              {...invalidProps(`${rowMessage}-${index}`, errors.at(`${index}.amount`))}
            />
            <select
              className="select-narrow"
              aria-label={t("dict.currency")}
              value={row.currency}
              onChange={(e) => setRow(index, { currency: e.target.value })}
            >
              {/* A row keeps its currency even if she no longer accepts it. */}
              {(currencies.includes(row.currency) ? currencies : [...currencies, row.currency]).map(
                (c) => (
                  <option key={c}>{c}</option>
                ),
              )}
            </select>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setRows((list) => list.filter((_, i) => i !== index))}
            >
              {t("dict.remove")}
            </button>
            <RowError id={`${rowMessage}-${index}`} error={errors.at(`${index}.amount`)} />
          </div>
        ))}
      </div>
      {errors.general ? <ErrorAlert error={errors.general} /> : null}
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
            setRows((list) => [
              ...list,
              { country_id: "", purpose_id: null, amount: "", currency: base },
            ])
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
        <dd>
          {money.tax_rate === null
            ? t("settings.taxNotSet")
            : t("dict.taxValue", { rate: Number(money.tax_rate) })}
        </dd>
        <dt>{t("dict.rateSource")}</dt>
        <dd>{t(`dict.source.${money.rate_source}`)}</dd>
      </dl>
      <p className="muted small">
        {t("dict.moneySettingsHint")} <Link to="/settings">{t("nav.account")}</Link>
      </p>
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

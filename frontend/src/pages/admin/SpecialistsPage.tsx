import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { apiGet, apiPatch, apiPost, errorCode } from "../../api";
import { IssuedPasswordCard, type IssuedPassword } from "../../components/IssuedPasswordCard";
import { ModulePicker, useModules } from "../../components/ModulePicker";

type Specialist = {
  tenant_id: string;
  name: string;
  email: string;
  base_currency: string;
  tax_rate: string;
  modules: string[];
  is_active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
};

const CURRENCIES = ["BYN", "PLN", "USD", "EUR"] as const;
type Currency = (typeof CURRENCIES)[number];
// НПД in Belarus; the admin changes it per specialist.
const DEFAULT_TAX_RATE = "10";
const KEY = ["admin", "specialists"] as const;

export function SpecialistsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const specialists = useQuery({
    queryKey: KEY,
    queryFn: () => apiGet<Specialist[]>("/admin/specialists"),
  });
  const [formOpen, setFormOpen] = useState(false);
  const [issued, setIssued] = useState<IssuedPassword | null>(null);
  const [editing, setEditing] = useState<Specialist | null>(null);
  const modules = useModules();

  const refresh = () => queryClient.invalidateQueries({ queryKey: KEY });

  const action = useMutation({
    mutationFn: ({ s, kind }: { s: Specialist; kind: "block" | "unblock" | "reset-password" }) =>
      apiPost<Specialist | { temporary_password: string }>(
        `/admin/specialists/${s.tenant_id}/${kind}`,
      ),
    onSuccess: (result, { s }) => {
      if ("temporary_password" in result) {
        setIssued({ email: s.email, password: result.temporary_password });
      }
      void refresh();
    },
  });

  const run = (s: Specialist, kind: "block" | "unblock" | "reset-password") => {
    const question = {
      block: "admin.confirmBlock",
      unblock: null,
      "reset-password": "admin.confirmReset",
    }[kind];
    if (question && !window.confirm(t(question, { email: s.email }))) return;
    action.mutate({ s, kind });
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("admin.specialistsTitle")}</h1>
          <div className="muted">{t("admin.specialistsSubtitle")}</div>
        </div>
        {!formOpen && (
          <button type="button" className="btn btn-p" onClick={() => setFormOpen(true)}>
            {t("admin.addSpecialist")}
          </button>
        )}
      </header>

      {issued && <IssuedPasswordCard issued={issued} onClose={() => setIssued(null)} />}

      {formOpen && (
        <NewSpecialistForm
          onCancel={() => setFormOpen(false)}
          onCreated={(email, password) => {
            setFormOpen(false);
            setIssued({ email, password });
            void refresh();
          }}
        />
      )}

      {editing && (
        <SpecialistSettings
          key={editing.tenant_id}
          specialist={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void refresh();
          }}
        />
      )}

      {action.isError && (
        <div className="alert alert-error" role="alert">
          {t(`errors.${errorCode(action.error)}`, { defaultValue: t("errors.unknown") })}
        </div>
      )}

      <section className="card">
        {specialists.isPending && <p className="muted">{t("common.loading")}</p>}
        {specialists.isError && <p className="alert alert-error">{t("errors.unknown")}</p>}
        {specialists.data?.length === 0 && <p className="muted">{t("admin.empty")}</p>}
        {specialists.data && specialists.data.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("admin.colName")}</th>
                  <th>{t("admin.colEmail")}</th>
                  <th>{t("admin.colAccounting")}</th>
                  <th>{t("admin.colModules")}</th>
                  <th>{t("admin.colStatus")}</th>
                  <th>{t("admin.colLastLogin")}</th>
                  <th aria-label={t("admin.colActions")} />
                </tr>
              </thead>
              <tbody>
                {specialists.data.map((s) => (
                  <tr key={s.tenant_id}>
                    <td>{s.name}</td>
                    <td>{s.email}</td>
                    <td className="nowrap">
                      {s.base_currency} · {Number(s.tax_rate)}%
                    </td>
                    <td className="nowrap">
                      {t("admin.modulesCount", {
                        on: s.modules.length,
                        total: modules.data?.length ?? "…",
                      })}
                    </td>
                    <td>
                      <StatusChip s={s} />
                    </td>
                    <td className="muted">
                      {s.last_login_at ? <DateTime value={s.last_login_at} /> : t("admin.never")}
                    </td>
                    <td>
                      <div className="actions">
                        <button type="button" className="btn btn-sm" onClick={() => setEditing(s)}>
                          {t("admin.settings")}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => run(s, "reset-password")}
                          disabled={action.isPending}
                        >
                          {t("admin.resetPassword")}
                        </button>
                        <button
                          type="button"
                          className={s.is_active ? "btn btn-sm btn-danger" : "btn btn-sm"}
                          onClick={() => run(s, s.is_active ? "block" : "unblock")}
                          disabled={action.isPending}
                        >
                          {t(s.is_active ? "admin.block" : "admin.unblock")}
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
    </div>
  );
}

function StatusChip({ s }: { s: Specialist }) {
  const { t } = useTranslation();
  if (!s.is_active) return <span className="chip st-red">{t("admin.statusBlocked")}</span>;
  if (s.must_change_password) {
    return <span className="chip st-orange">{t("admin.statusInvited")}</span>;
  }
  return <span className="chip st-green">{t("admin.statusActive")}</span>;
}

function DateTime({ value }: { value: string }) {
  const { i18n } = useTranslation();
  const text = new Intl.DateTimeFormat(i18n.language, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
  return <time dateTime={value}>{text}</time>;
}

function NewSpecialistForm({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: (email: string, password: string) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [currency, setCurrency] = useState<Currency>("BYN");
  const [taxRate, setTaxRate] = useState(DEFAULT_TAX_RATE);

  const create = useMutation({
    mutationFn: () =>
      apiPost<{ specialist: Specialist; temporary_password: string }>("/admin/specialists", {
        name,
        email,
        base_currency: currency,
        tax_rate: taxRate || "0",
      }),
    onSuccess: (result) => onCreated(result.specialist.email, result.temporary_password),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate();
  };

  return (
    <form className="card form-card" onSubmit={submit}>
      <h2 className="h2">{t("admin.newSpecialist")}</h2>
      <div className="form-grid">
        <label className="field">
          <span>{t("admin.colName")}</span>
          <input required autoFocus value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("auth.email")}</span>
          <input
            type="email"
            required
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="field">
          <span>{t("admin.colCurrency")}</span>
          <CurrencySelect value={currency} onChange={setCurrency} />
        </label>
        <TaxField value={taxRate} onChange={setTaxRate} />
      </div>
      <p className="muted small">{t("admin.newSpecialistHint")}</p>
      {create.isError && (
        <div className="alert alert-error" role="alert">
          {t(`errors.${errorCode(create.error)}`, { defaultValue: t("errors.unknown") })}
        </div>
      )}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={create.isPending || !name || !email}>
          {t("admin.create")}
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

function CurrencySelect({ value, onChange }: { value: Currency; onChange: (c: Currency) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as Currency)}>
      {CURRENCIES.map((c) => (
        <option key={c} value={c}>
          {c}
        </option>
      ))}
    </select>
  );
}

function TaxField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation();
  return (
    <label className="field">
      <span>{t("admin.taxRate")}</span>
      <input
        type="number"
        inputMode="decimal"
        min={0}
        max={100}
        step="0.01"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

/** Edit a specialist: name, accounting currency, tax and the modules they may use. */
function SpecialistSettings({
  specialist,
  onClose,
  onSaved,
}: {
  specialist: Specialist;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(specialist.name);
  const [currency, setCurrency] = useState(specialist.base_currency as Currency);
  const [taxRate, setTaxRate] = useState(String(Number(specialist.tax_rate)));
  const [modules, setModules] = useState(specialist.modules);

  const save = useMutation({
    mutationFn: () =>
      apiPatch<Specialist>(`/admin/specialists/${specialist.tenant_id}`, {
        name,
        base_currency: currency,
        tax_rate: taxRate || "0",
        modules,
      }),
    onSuccess: onSaved,
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form className="card form-card" onSubmit={submit}>
      <h2 className="h2">{t("admin.settingsTitle", { email: specialist.email })}</h2>
      <div className="form-grid">
        <label className="field">
          <span>{t("admin.colName")}</span>
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("admin.colCurrency")}</span>
          <CurrencySelect value={currency} onChange={setCurrency} />
        </label>
        <TaxField value={taxRate} onChange={setTaxRate} />
      </div>
      <h3 className="h3">{t("admin.modulesTitle")}</h3>
      <p className="muted small">{t("admin.modulesHint")}</p>
      <ModulePicker value={modules} onChange={setModules} />
      {save.isError && (
        <div className="alert alert-error" role="alert">
          {t(`errors.${errorCode(save.error)}`, { defaultValue: t("errors.unknown") })}
        </div>
      )}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={save.isPending || !name}>
          {t("common.save")}
        </button>
        <button type="button" className="btn" onClick={onClose}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

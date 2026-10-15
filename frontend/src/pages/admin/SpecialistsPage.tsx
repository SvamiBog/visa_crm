import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { apiGet, apiPost, errorCode } from "../../api";

type Specialist = {
  tenant_id: string;
  name: string;
  email: string;
  base_currency: string;
  is_active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
};

const CURRENCIES = ["BYN", "PLN", "USD", "EUR"] as const;
const KEY = ["admin", "specialists"] as const;

/** A temporary password is shown once: the admin passes it to the specialist. */
type IssuedPassword = { email: string; password: string };

export function SpecialistsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const specialists = useQuery({
    queryKey: KEY,
    queryFn: () => apiGet<Specialist[]>("/admin/specialists"),
  });
  const [formOpen, setFormOpen] = useState(false);
  const [issued, setIssued] = useState<IssuedPassword | null>(null);

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
                  <th>{t("admin.colCurrency")}</th>
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
                    <td>{s.base_currency}</td>
                    <td>
                      <StatusChip s={s} />
                    </td>
                    <td className="muted">
                      {s.last_login_at ? <DateTime value={s.last_login_at} /> : t("admin.never")}
                    </td>
                    <td>
                      <div className="actions">
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
  const [currency, setCurrency] = useState<(typeof CURRENCIES)[number]>("BYN");

  const create = useMutation({
    mutationFn: () =>
      apiPost<{ specialist: Specialist; temporary_password: string }>("/admin/specialists", {
        name,
        email,
        base_currency: currency,
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
          <select
            value={currency}
            onChange={(e) => setCurrency(e.target.value as (typeof CURRENCIES)[number])}
          >
            {CURRENCIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
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

function IssuedPasswordCard({ issued, onClose }: { issued: IssuedPassword; onClose: () => void }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(issued.password);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section className="card issued" role="status">
      <h2 className="h2">{t("admin.issuedTitle")}</h2>
      <p>{t("admin.issuedText", { email: issued.email })}</p>
      <div className="issued-row">
        <code className="issued-password">{issued.password}</code>
        <button type="button" className="btn btn-sm" onClick={() => void copy()}>
          {copied ? t("admin.copied") : t("admin.copy")}
        </button>
      </div>
      <p className="muted small">{t("admin.issuedWarning")}</p>
      <button type="button" className="btn btn-sm" onClick={onClose}>
        {t("admin.issuedDone")}
      </button>
    </section>
  );
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { apiGet, apiPatch, apiPost, errorCode } from "../../api";
import { IssuedPasswordCard, type IssuedPassword } from "../../components/IssuedPasswordCard";
import { ModulePicker } from "../../components/ModulePicker";
import { RevealedPasswords } from "./RevealedPasswords";

type Employee = {
  id: string;
  full_name: string | null;
  email: string;
  modules: string[];
  is_active: boolean;
  must_change_password: boolean;
  two_factor_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
  case_count: number;
  daily_card_limit: number;
  daily_password_limit: number;
};

type Action = "block" | "unblock" | "reset-password";

const KEY = ["employees"] as const;

/** The specialist's employees: invite, choose modules, block, issue a new password. */
export function EmployeesPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const employees = useQuery({ queryKey: KEY, queryFn: () => apiGet<Employee[]>("/employees") });
  // Employees can get only the modules the specialist has.
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<{ modules: string[]; timezone: string }>("/workspace"),
  });
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Employee | null>(null);
  const [issued, setIssued] = useState<IssuedPassword | null>(null);
  // Whose revealed vault passwords are open (Т.15): opens by itself after blocking.
  const [passwordsOf, setPasswordsOf] = useState<Employee | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: KEY });

  const action = useMutation({
    mutationFn: ({ e, kind }: { e: Employee; kind: Action }) =>
      apiPost<Employee | { temporary_password: string }>(`/employees/${e.id}/${kind}`),
    onSuccess: (result, { e, kind }) => {
      if ("temporary_password" in result) {
        setIssued({ email: e.email, password: result.temporary_password });
      }
      if (kind === "block") setPasswordsOf({ ...e, is_active: false });
      void refresh();
    },
  });

  const run = (e: Employee, kind: Action) => {
    const question = {
      block: "employees.confirmBlock",
      unblock: null,
      "reset-password": "employees.confirmReset",
    }[kind];
    if (question && !window.confirm(t(question, { email: e.email }))) return;
    action.mutate({ e, kind });
  };

  const available = workspace.data?.modules ?? [];

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("employees.title")}</h1>
          <div className="muted">{t("employees.subtitle")}</div>
          <div className="muted small">{t("employees.casesHint")}</div>
        </div>
        {!formOpen && (
          <button type="button" className="btn btn-p" onClick={() => setFormOpen(true)}>
            {t("employees.add")}
          </button>
        )}
      </header>

      {issued && <IssuedPasswordCard issued={issued} onClose={() => setIssued(null)} />}

      {formOpen && (
        <EmployeeForm
          available={available}
          onCancel={() => setFormOpen(false)}
          onSaved={(created) => {
            setFormOpen(false);
            if (created) setIssued(created);
            void refresh();
          }}
        />
      )}

      {editing && (
        <EmployeeForm
          key={editing.id}
          employee={editing}
          available={available}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void refresh();
          }}
        />
      )}

      {passwordsOf && (
        <RevealedPasswords
          key={passwordsOf.id}
          employee={passwordsOf}
          onClose={() => setPasswordsOf(null)}
        />
      )}

      {action.isError && (
        <div className="alert alert-error" role="alert">
          {t(`errors.${errorCode(action.error)}`, { defaultValue: t("errors.unknown") })}
        </div>
      )}

      <section className="card">
        {employees.isPending && <p className="muted">{t("common.loading")}</p>}
        {employees.isError && <p className="alert alert-error">{t("errors.unknown")}</p>}
        {employees.data?.length === 0 && <p className="muted">{t("employees.empty")}</p>}
        {employees.data && employees.data.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("employees.colName")}</th>
                  <th>{t("auth.email")}</th>
                  <th>{t("admin.colModules")}</th>
                  <th>{t("employees.colCases")}</th>
                  <th>{t("admin.colStatus")}</th>
                  <th>{t("employees.colTwoFactor")}</th>
                  <th aria-label={t("admin.colActions")} />
                </tr>
              </thead>
              <tbody>
                {employees.data.map((e) => (
                  <tr key={e.id}>
                    <td>{e.full_name}</td>
                    <td>{e.email}</td>
                    <td className="muted">
                      {e.modules.length > 0
                        ? e.modules.map((m) => t(`modules.name.${m}`)).join(", ")
                        : t("employees.noModules")}
                    </td>
                    <td className="muted">{e.case_count}</td>
                    <td>
                      <StatusChip e={e} />
                    </td>
                    <td className="muted">
                      {t(e.two_factor_enabled ? "employees.twoFactorOn" : "employees.twoFactorOff")}
                    </td>
                    <td>
                      <div className="actions">
                        <button type="button" className="btn btn-sm" onClick={() => setEditing(e)}>
                          {t("admin.settings")}
                        </button>
                        <button type="button" className="btn btn-sm" onClick={() => setPasswordsOf(e)}>
                          {t("revealed.open")}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => run(e, "reset-password")}
                          disabled={action.isPending}
                        >
                          {t("admin.resetPassword")}
                        </button>
                        <button
                          type="button"
                          className={e.is_active ? "btn btn-sm btn-danger" : "btn btn-sm"}
                          onClick={() => run(e, e.is_active ? "block" : "unblock")}
                          disabled={action.isPending}
                        >
                          {t(e.is_active ? "admin.block" : "admin.unblock")}
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

function StatusChip({ e }: { e: Employee }) {
  const { t } = useTranslation();
  if (!e.is_active) return <span className="chip st-red">{t("admin.statusBlocked")}</span>;
  if (e.must_change_password) {
    return <span className="chip st-orange">{t("admin.statusInvited")}</span>;
  }
  return <span className="chip st-green">{t("admin.statusActive")}</span>;
}

/** Invite a new employee (no `employee`) or change an existing one's name and modules. */
function EmployeeForm({
  employee,
  available,
  onCancel,
  onSaved,
}: {
  employee?: Employee;
  available: string[];
  onCancel: () => void;
  onSaved: (issued?: IssuedPassword) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(employee?.full_name ?? "");
  const [email, setEmail] = useState(employee?.email ?? "");
  const [modules, setModules] = useState(
    employee?.modules.filter((m) => available.includes(m)) ?? [],
  );
  // Daily limits (backlog Т.14): set for an existing employee, defaults for a new one.
  const [cardLimit, setCardLimit] = useState(String(employee?.daily_card_limit ?? ""));
  const [passwordLimit, setPasswordLimit] = useState(
    String(employee?.daily_password_limit ?? ""),
  );

  const save = useMutation({
    mutationFn: async (): Promise<IssuedPassword | undefined> => {
      if (employee) {
        await apiPatch<Employee>(`/employees/${employee.id}`, {
          full_name: name,
          modules,
          daily_card_limit: Number(cardLimit),
          daily_password_limit: Number(passwordLimit),
        });
        return undefined;
      }
      const created = await apiPost<{ employee: Employee; temporary_password: string }>(
        "/employees",
        { full_name: name, email, modules },
      );
      return { email: created.employee.email, password: created.temporary_password };
    },
    onSuccess: onSaved,
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form className="card form-card" onSubmit={submit}>
      <h2 className="h2">
        {employee ? t("employees.settingsTitle", { email: employee.email }) : t("employees.new")}
      </h2>
      <div className="form-grid">
        <label className="field">
          <span>{t("employees.colName")}</span>
          <input required autoFocus value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        {!employee && (
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
        )}
      </div>
      <h3 className="h3">{t("employees.modulesTitle")}</h3>
      <p className="muted small">{t("employees.modulesHint")}</p>
      <ModulePicker value={modules} onChange={setModules} allowed={available} />
      {employee && (
        <>
          <h3 className="h3">{t("employees.limitsTitle")}</h3>
          <p className="muted small">{t("employees.limitsHint")}</p>
          <div className="form-grid">
            <label className="field">
              <span>{t("employees.cardLimit")}</span>
              <input
                type="number"
                min={1}
                max={1000}
                required
                value={cardLimit}
                onChange={(e) => setCardLimit(e.target.value)}
              />
            </label>
            <label className="field">
              <span>{t("employees.passwordLimit")}</span>
              <input
                type="number"
                min={1}
                max={1000}
                required
                value={passwordLimit}
                onChange={(e) => setPasswordLimit(e.target.value)}
              />
            </label>
          </div>
        </>
      )}
      {!employee && <p className="muted small">{t("employees.newHint")}</p>}
      {save.isError && (
        <div className="alert alert-error" role="alert">
          {t(`errors.${errorCode(save.error)}`, { defaultValue: t("errors.unknown") })}
        </div>
      )}
      <div className="form-actions">
        <button
          type="submit"
          className="btn btn-p"
          disabled={save.isPending || !name || (!employee && !email)}
        >
          {employee ? t("common.save") : t("employees.invite")}
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

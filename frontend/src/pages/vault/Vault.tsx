import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { apiDelete, apiGet, apiPatch, apiPost } from "../../api";
import { EMAIL_OWNERS, type EmailOwner, type VisaCase } from "../../contacts";
import { byId, useCountries, useLocalName } from "../../reference";
import {
  rulesSummary,
  useOwnVault,
  useVaultSystems,
  vaultKey,
  type VaultEntry,
} from "../../vault";
import { ErrorAlert, Row } from "../clients/ui";

// The vault's blocks (backlog 1.7): a client's accounts on their card and the
// workspace's own accounts on the «Сейф» page.

/** A shown password hides itself after this long. */
const HIDE_AFTER_MS = 30_000;

type PasswordOut = { password: string };
export type CaseOption = { id: string; label: string };

/** Case labels for the account form and chips: «Кейс №3 · Польша». */
export function useCaseOptions(cases: VisaCase[]): CaseOption[] {
  const { t } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  return cases.map((c) => ({
    id: c.id,
    label: `${t("cases.number", { number: c.number })} · ${name.country(countryById.get(c.destination_id))}`,
  }));
}

function useRefreshVault() {
  const queryClient = useQueryClient();
  // Every list: an account changed on a card also changes the own accounts' lists.
  return () => void queryClient.invalidateQueries({ queryKey: ["vault"] });
}

/** «Сейф доступов» on a client's card. */
export function ContactVault({ contactId, cases }: { contactId: string; cases: VisaCase[] }) {
  const { t } = useTranslation();
  const entries = useQuery({
    queryKey: vaultKey(contactId),
    queryFn: () => apiGet<VaultEntry[]>(`/contacts/${contactId}/vault`),
  });
  const systems = useVaultSystems();
  const systemById = useMemo(() => byId(systems.data), [systems.data]);
  const caseOptions = useCaseOptions(cases);
  const caseById = useMemo(() => byId(caseOptions), [caseOptions]);
  const [editing, setEditing] = useState<VaultEntry | "new" | null>(null);

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="h2">{t("vault.title")}</h2>
        {editing === null && (
          <button type="button" className="btn btn-sm btn-p" onClick={() => setEditing("new")}>
            {t("vault.add")}
          </button>
        )}
      </div>
      {editing !== null && (
        <VaultEntryForm
          key={editing === "new" ? "new" : editing.id}
          entry={editing === "new" ? undefined : editing}
          contactId={contactId}
          cases={caseOptions}
          onDone={() => setEditing(null)}
        />
      )}
      {entries.isPending && <p className="muted">{t("common.loading")}</p>}
      {entries.isError && <ErrorAlert error={entries.error} />}
      {entries.data?.length === 0 && editing === null && (
        <p className="muted small">{t("vault.empty")}</p>
      )}
      <div className="vault-list">
        {entries.data?.map((entry) => (
          <VaultEntryView
            key={entry.id}
            entry={entry}
            systemName={systemById.get(entry.system_id)?.name ?? "—"}
            caseLabel={entry.case_id ? caseById.get(entry.case_id)?.label : undefined}
            onEdit={() => setEditing(entry)}
          />
        ))}
      </div>
      {entries.data && entries.data.length > 0 && (
        <p className="muted small">{t("vault.logged")}</p>
      )}
    </section>
  );
}

/** One account. The password is fetched only by «show» or «copy», and each time the
 * fetch is written to the audit log. */
export function VaultEntryView({
  entry,
  systemName,
  caseLabel,
  onEdit,
  children,
}: {
  entry: VaultEntry;
  systemName: string;
  caseLabel?: string;
  onEdit: () => void;
  children?: ReactNode;
}) {
  const { t, i18n } = useTranslation();
  const refresh = useRefreshVault();
  const [shown, setShown] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const reveal = useMutation({
    mutationFn: () => apiPost<PasswordOut>(`/vault/entries/${entry.id}/reveal`),
  });
  const remove = useMutation({
    mutationFn: () => apiDelete<void>(`/vault/entries/${entry.id}`),
    onSuccess: refresh,
  });

  useEffect(() => {
    if (shown === null) return;
    const timer = window.setTimeout(() => setShown(null), HIDE_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [shown]);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);
  // A changed password is not the one on the screen.
  useEffect(() => setShown(null), [entry.password_updated_at]);

  const copy = async () => {
    let password = shown;
    try {
      password ??= (await reveal.mutateAsync()).password;
    } catch {
      return; // the error is shown under the account
    }
    try {
      await navigator.clipboard.writeText(password);
      setCopied(true);
    } catch {
      setShown(password); // no clipboard (e.g. not https): show it to copy by hand
    }
  };
  const savedOn = entry.password_updated_at
    ? new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium" }).format(
        new Date(entry.password_updated_at),
      )
    : null;

  return (
    <article className="vault-entry">
      <div className="card-head">
        <h3 className="h3">{systemName}</h3>
        <span className="vault-tags">
          {caseLabel && <span className="chip st-blue">{caseLabel}</span>}
          {entry.created_manually && (
            <span className="chip st-gray">{t("vault.createdManually")}</span>
          )}
        </span>
      </div>
      <Row label={t("vault.login")}>
        <span className="mono">{entry.login}</span>
      </Row>
      {entry.email_owner && (
        <Row label={t("queues.ownerLabel")}>{t(`queues.owner.${entry.email_owner}`)}</Row>
      )}
      {entry.extra_id && <Row label={t("vault.extraId")}>{entry.extra_id}</Row>}
      <div className="kv">
        <span className="muted">{t("vault.password")}</span>
        {entry.has_password ? (
          <span className="secret">
            <span className="mono">{shown ?? "••••••••"}</span>
            <button
              type="button"
              className="link-button"
              disabled={reveal.isPending}
              onClick={() =>
                shown === null
                  ? reveal.mutate(undefined, { onSuccess: (r) => setShown(r.password) })
                  : setShown(null)
              }
            >
              {t(shown === null ? "vault.show" : "vault.hide")}
            </button>
            <button
              type="button"
              className="link-button"
              disabled={reveal.isPending}
              onClick={() => void copy()}
            >
              {t(copied ? "vault.copied" : "vault.copy")}
            </button>
          </span>
        ) : (
          <span className="muted">{t("vault.noPassword")}</span>
        )}
      </div>
      {savedOn && <p className="muted small">{t("vault.savedOn", { date: savedOn })}</p>}
      {entry.note && <p className="note-body small">{entry.note}</p>}
      {children}
      {(reveal.isError || remove.isError) && <ErrorAlert error={reveal.error ?? remove.error} />}
      <div className="note-actions">
        <button type="button" className="link-button" onClick={onEdit}>
          {t("reference.edit")}
        </button>
        <button
          type="button"
          className="link-button"
          disabled={remove.isPending}
          onClick={() => {
            if (window.confirm(t("vault.confirmDelete"))) remove.mutate();
          }}
        >
          {t("vault.delete")}
        </button>
      </div>
    </article>
  );
}

/** A new account or changes to one. `contactId` null: the workspace's own account. */
export function VaultEntryForm({
  entry,
  contactId,
  cases = [],
  onDone,
}: {
  entry?: VaultEntry;
  contactId: string | null;
  cases?: CaseOption[];
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const refresh = useRefreshVault();
  const systems = useVaultSystems();
  const choices = (systems.data ?? []).filter((s) => s.is_active || s.id === entry?.system_id);
  const [systemId, setSystemId] = useState(entry?.system_id ?? "");
  const [caseId, setCaseId] = useState(entry?.case_id ?? "");
  const [login, setLogin] = useState(entry?.login ?? "");
  const [owner, setOwner] = useState<EmailOwner | "">(entry?.email_owner ?? "");
  const [password, setPassword] = useState("");
  const [extraId, setExtraId] = useState(entry?.extra_id ?? "");
  const [manual, setManual] = useState(entry?.created_manually ?? false);
  const [note, setNote] = useState(entry?.note ?? "");
  const chosen = systemId || choices[0]?.id || "";
  const system = choices.find((s) => s.id === chosen);
  // On the specialist's backup mailbox: offer the mailboxes kept as own accounts.
  const onBackup = contactId !== null && owner === "specialist_backup";
  const own = useOwnVault(onBackup);
  const kinds = useMemo(() => byId(systems.data), [systems.data]);
  const mailboxes = (own.data ?? []).filter((e) => kinds.get(e.system_id)?.kind === "email");
  const listId = useId();
  const passwordId = useId();

  const generate = useMutation({
    mutationFn: () => apiPost<PasswordOut>("/vault/password", { system_id: chosen || null }),
    onSuccess: (r) => setPassword(r.password),
  });
  const save = useMutation({
    mutationFn: () => {
      const body = {
        system_id: chosen,
        case_id: caseId || null,
        login: login.trim(),
        email_owner: owner || null,
        created_manually: manual,
        extra_id: extraId.trim() || null,
        note: note.trim() || null,
        // Empty when editing: the stored password stays.
        ...(password ? { password } : {}),
      };
      return entry
        ? apiPatch<VaultEntry>(`/vault/entries/${entry.id}`, body)
        : apiPost<VaultEntry>("/vault/entries", { ...body, contact_id: contactId });
    },
    onSuccess: () => {
      refresh();
      onDone();
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form className="form-card vault-form" onSubmit={submit}>
      <div className="form-grid">
        <label className="field">
          <span>{t("vault.system")}</span>
          <select required value={chosen} onChange={(e) => setSystemId(e.target.value)}>
            {choices.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        {contactId !== null && cases.length > 0 && (
          <label className="field">
            <span>{t("vault.case")}</span>
            <select value={caseId} onChange={(e) => setCaseId(e.target.value)}>
              <option value="">{t("vault.noCase")}</option>
              {cases.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span>{t("queues.ownerLabel")}</span>
          <select value={owner} onChange={(e) => setOwner(e.target.value as EmailOwner | "")}>
            <option value="">—</option>
            {EMAIL_OWNERS.map((o) => (
              <option key={o} value={o}>
                {t(`queues.owner.${o}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("vault.login")}</span>
          <input
            required
            maxLength={320}
            autoComplete="off"
            spellCheck={false}
            list={onBackup ? listId : undefined}
            value={login}
            onChange={(e) => setLogin(e.target.value)}
          />
          {onBackup && (
            <datalist id={listId}>
              {mailboxes.map((m) => (
                <option key={m.id} value={m.login} />
              ))}
            </datalist>
          )}
        </label>
        <div className="field">
          <label htmlFor={passwordId}>
            {t(entry?.has_password ? "vault.newPassword" : "vault.password")}
          </label>
          <div className="input-action">
            {/* Plain text: the password is typed into the site from here. Not offered to
                the browser's password manager. */}
            <input
              id={passwordId}
              className="mono"
              maxLength={200}
              autoComplete="off"
              spellCheck={false}
              data-1p-ignore
              data-lpignore="true"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button
              type="button"
              className="btn btn-sm"
              disabled={generate.isPending}
              onClick={() => generate.mutate()}
            >
              {t("vault.generate")}
            </button>
          </div>
          <small className="muted">
            {entry?.has_password && `${t("vault.keepPassword")} `}
            {system && t("vault.rules", { rules: rulesSummary(system) })}
          </small>
        </div>
        <label className="field">
          <span>{t("vault.extraId")}</span>
          <input maxLength={100} value={extraId} onChange={(e) => setExtraId(e.target.value)} />
        </label>
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={manual} onChange={(e) => setManual(e.target.checked)} />
        <span>{t("vault.createdManuallyLabel")}</span>
      </label>
      <label className="field">
        <span>{t("vault.note")}</span>
        <textarea rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      {(save.isError || generate.isError) && <ErrorAlert error={save.error ?? generate.error} />}
      <div className="form-actions">
        <button type="submit" className="btn btn-sm btn-p" disabled={save.isPending || !chosen}>
          {t("common.save")}
        </button>
        <button type="button" className="btn btn-sm" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

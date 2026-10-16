import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { byId } from "../../reference";
import { useOwnVault, useVaultSystems, type VaultEntry } from "../../vault";
import { ErrorAlert } from "../clients/ui";
import { VaultEntryForm, VaultEntryView } from "./Vault";

/** «Сейф»: the workspace's own accounts — the specialist's backup mailboxes, Booking —
 * and, for each, the clients' accounts made on it. Clients' accounts live on their cards. */
export function VaultPage() {
  const { t } = useTranslation();
  const own = useOwnVault();
  const systems = useVaultSystems();
  const systemById = useMemo(() => byId(systems.data), [systems.data]);
  const [editing, setEditing] = useState<VaultEntry | "new" | null>(null);
  const systemName = (id: string) => systemById.get(id)?.name ?? "—";

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("vault.pageTitle")}</h1>
          <div className="muted">{t("vault.pageSubtitle")}</div>
        </div>
        {editing === null && (
          <button type="button" className="btn btn-p" onClick={() => setEditing("new")}>
            {t("vault.addOwn")}
          </button>
        )}
      </header>

      {editing !== null && (
        <section className="card">
          <h2 className="h2">
            {editing === "new" ? t("vault.newOwn") : systemName(editing.system_id)}
          </h2>
          <VaultEntryForm
            key={editing === "new" ? "new" : editing.id}
            entry={editing === "new" ? undefined : editing}
            contactId={null}
            onDone={() => setEditing(null)}
          />
        </section>
      )}

      <section className="card">
        {own.isPending && <p className="muted">{t("common.loading")}</p>}
        {own.isError && <ErrorAlert error={own.error} />}
        {own.data?.length === 0 && <p className="muted">{t("vault.ownEmpty")}</p>}
        <div className="vault-list">
          {own.data?.map((entry) => (
            <VaultEntryView
              key={entry.id}
              entry={entry}
              systemName={systemName(entry.system_id)}
              onEdit={() => setEditing(entry)}
            >
              <div className="kv">
                <span className="muted">{t("vault.accountsOnIt")}</span>
                <span>
                  {entry.accounts.length === 0
                    ? "—"
                    : entry.accounts.map((a) => (
                        <div key={a.id}>
                          <Link to={`/app/clients/${a.contact_id}`}>{a.contact_name}</Link>
                          <span className="muted"> · {systemName(a.system_id)}</span>
                        </div>
                      ))}
                </span>
              </div>
            </VaultEntryView>
          ))}
        </div>
        {own.data && own.data.length > 0 && <p className="muted small">{t("vault.logged")}</p>}
      </section>
    </div>
  );
}

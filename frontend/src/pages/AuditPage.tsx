import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { apiGet } from "../api";

type AuditEvent = {
  id: number;
  occurred_at: string;
  tenant_id: string | null;
  actor_id: string | null;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, string>;
  ip: string | null;
};

type AuditPage = { events: AuditEvent[]; next_cursor: number | null };
type Actor = { id: string; email: string };
type Workspace = { tenant_id: string; name: string; email: string };

type Filters = {
  workspace: string; // "" — all, "platform" — outside any workspace, or a tenant id
  actor: string;
  category: string;
  action: string;
  from: string;
  to: string;
  order: "desc" | "asc";
};
const NO_FILTERS: Filters = {
  workspace: "",
  actor: "",
  category: "",
  action: "",
  from: "",
  to: "",
  order: "desc",
};

/** The start of a day in the browser's calendar, as an instant for the API. */
function dayStart(day: string, plusDays = 0): string {
  const date = new Date(`${day}T00:00:00`);
  date.setDate(date.getDate() + plusDays);
  return date.toISOString();
}

function queryString(filters: Filters, cursor: number | null): string {
  const params = new URLSearchParams({ order: filters.order });
  if (filters.workspace === "platform") params.set("platform", "true");
  else if (filters.workspace) params.set("tenant_id", filters.workspace);
  if (filters.actor) params.set("actor_id", filters.actor);
  if (filters.action) params.set("action", filters.action);
  else if (filters.category) params.set("category", filters.category);
  if (filters.from) params.set("occurred_from", dayStart(filters.from));
  if (filters.to) params.set("occurred_to", dayStart(filters.to, 1)); // the whole last day
  if (cursor !== null) params.set("cursor", String(cursor));
  return params.toString();
}

/** The audit log: the admin sees and filters the whole platform; a specialist sees what
 * they and their employees did. */
export function AuditPage({
  scope,
  timeZone,
}: {
  scope: "admin" | "workspace";
  timeZone?: string;
}) {
  const { t } = useTranslation();
  const admin = scope === "admin";
  const path = admin ? "/admin/audit" : "/audit";
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const set = (changes: Partial<Filters>) => setFilters((f) => ({ ...f, ...changes }));

  const actions = useQuery({
    queryKey: ["audit", "actions"],
    queryFn: () => apiGet<string[]>("/audit/actions"),
  });
  const actors = useQuery({
    queryKey: ["audit", scope, "actors"],
    queryFn: () => apiGet<Actor[]>(`${path}/actors`),
  });
  const workspaces = useQuery({
    queryKey: ["admin", "specialists"],
    queryFn: () => apiGet<Workspace[]>("/admin/specialists"),
    enabled: admin,
  });
  const workspaceName = useMemo(
    () => new Map((workspaces.data ?? []).map((w) => [w.tenant_id, w.name])),
    [workspaces.data],
  );
  const categories = useMemo(
    () => [...new Set((actions.data ?? []).map((a) => a.split(".")[0]))],
    [actions.data],
  );
  const shownActions = (actions.data ?? []).filter(
    (a) => !filters.category || a.startsWith(`${filters.category}.`),
  );

  const log = useInfiniteQuery({
    queryKey: ["audit", scope, filters],
    queryFn: ({ pageParam }) => apiGet<AuditPage>(`${path}?${queryString(filters, pageParam)}`),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const events = log.data?.pages.flatMap((page) => page.events) ?? [];
  const filtered = Object.entries(filters).some(
    ([key, value]) => value !== NO_FILTERS[key as keyof Filters],
  );

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("audit.title")}</h1>
          <div className="muted">{t(admin ? "audit.subtitleAdmin" : "audit.subtitleWorkspace")}</div>
        </div>
      </header>

      <div className="filter-bar">
        {admin && (
          <label className="field">
            <span>{t("audit.workspace")}</span>
            <select value={filters.workspace} onChange={(e) => set({ workspace: e.target.value })}>
              <option value="">{t("index.any")}</option>
              <option value="platform">{t("audit.platformOnly")}</option>
              {(workspaces.data ?? []).map((w) => (
                <option key={w.tenant_id} value={w.tenant_id}>
                  {w.name} · {w.email}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span>{t("audit.colActor")}</span>
          <select value={filters.actor} onChange={(e) => set({ actor: e.target.value })}>
            <option value="">{t("index.any")}</option>
            {(actors.data ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.email}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("audit.category")}</span>
          <select
            value={filters.category}
            onChange={(e) => set({ category: e.target.value, action: "" })}
          >
            <option value="">{t("index.any")}</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {t(`audit.categories.${c}`, { defaultValue: c })}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("audit.colAction")}</span>
          <select value={filters.action} onChange={(e) => set({ action: e.target.value })}>
            <option value="">{t("index.any")}</option>
            {shownActions.map((a) => (
              <option key={a} value={a}>
                {t(`audit.action.${a}`, { defaultValue: a })}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("audit.from")}</span>
          <input
            type="date"
            value={filters.from}
            max={filters.to || undefined}
            onChange={(e) => set({ from: e.target.value })}
          />
        </label>
        <label className="field">
          <span>{t("audit.to")}</span>
          <input
            type="date"
            value={filters.to}
            min={filters.from || undefined}
            onChange={(e) => set({ to: e.target.value })}
          />
        </label>
        <label className="field">
          <span>{t("audit.order")}</span>
          <select
            value={filters.order}
            onChange={(e) => set({ order: e.target.value as Filters["order"] })}
          >
            <option value="desc">{t("audit.newestFirst")}</option>
            <option value="asc">{t("audit.oldestFirst")}</option>
          </select>
        </label>
        {filtered && (
          <button type="button" className="btn btn-sm" onClick={() => setFilters(NO_FILTERS)}>
            {t("index.reset")}
          </button>
        )}
      </div>

      <section className="card">
        {log.isPending && <p className="muted">{t("common.loading")}</p>}
        {log.isError && <p className="alert alert-error">{t("errors.unknown")}</p>}
        {log.isSuccess && events.length === 0 && (
          <p className="muted">{t(filtered ? "audit.nothingFound" : "audit.empty")}</p>
        )}
        {events.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("audit.colTime")}</th>
                  {admin && <th>{t("audit.workspace")}</th>}
                  <th>{t("audit.colActor")}</th>
                  <th>{t("audit.colAction")}</th>
                  <th>{t("audit.colDetails")}</th>
                  <th>{t("audit.colIp")}</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.id}>
                    <td className="nowrap">
                      <EventTime value={event.occurred_at} timeZone={timeZone} />
                    </td>
                    {admin && (
                      <td className="muted">
                        {event.tenant_id
                          ? (workspaceName.get(event.tenant_id) ?? "—")
                          : t("audit.platform")}
                      </td>
                    )}
                    <td>{event.actor_email ?? t("audit.system")}</td>
                    <td>{t(`audit.action.${event.action}`, { defaultValue: event.action })}</td>
                    <td className="muted">
                      <EventDetails event={event} />
                    </td>
                    <td className="muted nowrap">{event.ip ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {log.hasNextPage && (
          <button
            type="button"
            className="btn btn-sm load-more"
            onClick={() => void log.fetchNextPage()}
            disabled={log.isFetchingNextPage}
          >
            {t("audit.loadMore")}
          </button>
        )}
      </section>
    </div>
  );
}

function EventTime({ value, timeZone }: { value: string; timeZone?: string }) {
  const { i18n } = useTranslation();
  const text = new Intl.DateTimeFormat(i18n.language, {
    dateStyle: "short",
    timeStyle: "medium",
    timeZone,
  }).format(new Date(value));
  return <time dateTime={value}>{text}</time>;
}

function EventDetails({ event }: { event: AuditEvent }) {
  const { t } = useTranslation();
  const { method, reason, name, email, kind } = event.details;
  if (method) return <>{t(`audit.method.${method}`, { defaultValue: method })}</>;
  // Consent and contract files: which paper (the file name is not logged).
  if (kind && event.action.startsWith("contacts.document_")) return <>{t(`contacts.${kind}`)}</>;
  if (reason) return <>{t(`audit.reason.${reason}`, { defaultValue: reason })}</>;
  if (name && email) return <>{`${name} · ${email}`}</>;
  if (email) return <>{email}</>;
  if (name && event.target_type) {
    const target = t(`audit.target.${event.target_type}`, { defaultValue: event.target_type });
    return <>{`${target}: ${name}`}</>;
  }
  return null;
}

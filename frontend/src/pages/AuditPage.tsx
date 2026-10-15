import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiGet } from "../api";

type AuditEvent = {
  id: number;
  occurred_at: string;
  tenant_id: string | null;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, string>;
  ip: string | null;
};

type AuditPage = { events: AuditEvent[]; next_before: number | null };

/** The audit log: the admin sees the whole platform, a specialist their workspace. */
export function AuditPage({
  scope,
  timeZone,
}: {
  scope: "admin" | "workspace";
  timeZone?: string;
}) {
  const { t } = useTranslation();
  const path = scope === "admin" ? "/admin/audit" : "/audit";
  const log = useInfiniteQuery({
    queryKey: ["audit", scope],
    queryFn: ({ pageParam }) => apiGet<AuditPage>(pageParam ? `${path}?before=${pageParam}` : path),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_before,
  });
  const events = log.data?.pages.flatMap((page) => page.events) ?? [];

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("audit.title")}</h1>
          <div className="muted">
            {t(scope === "admin" ? "audit.subtitleAdmin" : "audit.subtitleWorkspace")}
          </div>
        </div>
      </header>

      <section className="card">
        {log.isPending && <p className="muted">{t("common.loading")}</p>}
        {log.isError && <p className="alert alert-error">{t("errors.unknown")}</p>}
        {log.isSuccess && events.length === 0 && <p className="muted">{t("audit.empty")}</p>}
        {events.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("audit.colTime")}</th>
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
  const { method, reason, name, email } = event.details;
  if (method) return <>{t(`audit.method.${method}`, { defaultValue: method })}</>;
  if (reason) return <>{t(`audit.reason.${reason}`, { defaultValue: reason })}</>;
  if (name && email) return <>{`${name} · ${email}`}</>;
  if (email) return <>{email}</>;
  return null;
}

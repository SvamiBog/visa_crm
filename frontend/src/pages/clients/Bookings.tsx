import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { apiDelete, apiGet, apiPatch, apiPost } from "../../api";
import {
  BOOKING_KINDS,
  BOOKING_STATUSES,
  formatDate,
  type Booking,
  type BookingKind,
  type BookingStatus,
  type VisaCase,
} from "../../contacts";
import { byId } from "../../reference";
import { useOwnVault, useVaultSystems, vaultKey, type VaultEntry } from "../../vault";
import { ErrorAlert } from "./ui";

// Bookings for the document package (backlog 1.8): hotels with their free cancellation
// deadline and the client's flight booking with its validity.

export const BOOKING_STATUS_CHIP: Record<BookingStatus, string> = {
  active: "chip st-blue",
  rescheduled: "chip st-orange",
  cancelled: "chip st-gray",
};

/** «через 3 дн.», «завтра», «прошёл»: how close the deadline is. */
export function DeadlineChip({ days }: { days: number | null }) {
  const { t } = useTranslation();
  if (days === null) return null;
  const cls = days <= 1 ? "chip st-red" : days <= 3 ? "chip st-orange" : "chip st-gray";
  const text =
    days < 0
      ? t("bookings.deadlinePassed")
      : days === 0
        ? t("bookings.deadlineToday")
        : days === 1
          ? t("bookings.deadlineTomorrow")
          : t("bookings.daysLeft", { count: days });
  return <span className={cls}>{text}</span>;
}

function useHasVault(): boolean {
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<{ modules: string[] }>("/workspace"),
  });
  return workspace.data?.modules.includes("vault") ?? false;
}

/** Accounts a booking can be made in — the workspace's own and the client's, on booking
 * sites — and labels of every account («Booking.com · login»). Empty without the vault. */
export function useBookingAccounts(contactId: string | null) {
  const hasVault = useHasVault();
  const systems = useVaultSystems();
  const own = useOwnVault(hasVault);
  const client = useQuery({
    queryKey: vaultKey(contactId),
    queryFn: () => apiGet<VaultEntry[]>(`/contacts/${contactId}/vault`),
    enabled: hasVault && contactId !== null,
  });
  return useMemo(() => {
    const systemById = byId(systems.data);
    const entries = [...(own.data ?? []), ...(client.data ?? [])];
    const label = (e: VaultEntry) => `${systemById.get(e.system_id)?.name ?? "—"} · ${e.login}`;
    // Booking sites (and other sites), not mailboxes or visa centers.
    const kinds = new Set(["booking", "other"]);
    const choices = entries.filter((e) => kinds.has(systemById.get(e.system_id)?.kind ?? ""));
    return {
      enabled: hasVault,
      options: choices.map((e) => ({ id: e.id, label: label(e) })),
      labels: new Map(entries.map((e) => [e.id, label(e)])),
    };
  }, [hasVault, systems.data, own.data, client.data]);
}

function useRefreshCards() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["contacts"] });
    void queryClient.invalidateQueries({ queryKey: ["bookings"] });
  };
}

/** The «dates» of a booking: 03.12.2026 – 07.12.2026. */
export function useStay() {
  const { i18n } = useTranslation();
  return (b: Pick<Booking, "starts_on" | "ends_on">) =>
    b.starts_on || b.ends_on
      ? `${formatDate(b.starts_on, i18n.language) || "…"} – ${formatDate(b.ends_on, i18n.language) || "…"}`
      : null;
}

export function CaseBookings({ visaCase: c }: { visaCase: VisaCase }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState<Booking | "new" | null>(null);
  const accounts = useBookingAccounts(c.contact_id);
  const live = c.bookings.filter((b) => b.status !== "cancelled");
  const unclosed = live.filter((b) => b.warnings.includes("case_closed")).length;

  return (
    <section className="bookings">
      <div className="card-head">
        <h4 className="h3">
          {t("bookings.title")}
          {c.bookings.length > 0 && (
            <span className="muted">
              {" · "}
              {t("bookings.count", { count: live.length, total: c.bookings.length })}
            </span>
          )}
        </h4>
        {editing === null && (
          <button type="button" className="btn btn-sm" onClick={() => setEditing("new")}>
            {t("bookings.add")}
          </button>
        )}
      </div>
      {unclosed > 0 && (
        <div className="alert alert-warn" role="alert">
          {t("bookings.cancelOnClose", { count: unclosed })}
        </div>
      )}
      {editing !== null && (
        <BookingForm
          key={editing === "new" ? "new" : editing.id}
          caseId={c.id}
          booking={editing === "new" ? undefined : editing}
          accounts={accounts}
          onDone={() => setEditing(null)}
        />
      )}
      {c.bookings.length === 0 && editing === null && (
        <p className="muted small">{t("bookings.empty")}</p>
      )}
      <ul className="booking-list">
        {c.bookings.map((b) => (
          <BookingItem
            key={b.id}
            booking={b}
            account={b.vault_entry_id ? accounts.labels.get(b.vault_entry_id) : undefined}
            onEdit={() => setEditing(b)}
          />
        ))}
      </ul>
    </section>
  );
}

function BookingItem({
  booking: b,
  account,
  onEdit,
}: {
  booking: Booking;
  account?: string;
  onEdit: () => void;
}) {
  const { t, i18n } = useTranslation();
  const refresh = useRefreshCards();
  const stay = useStay();
  const update = useMutation({
    mutationFn: (body: object) => apiPatch<Booking>(`/bookings/${b.id}`, body),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => apiDelete<void>(`/bookings/${b.id}`),
    onSuccess: refresh,
  });
  const live = b.status !== "cancelled";

  return (
    <li className={live ? "booking-item" : "booking-item row-off"}>
      <div className="booking-line">
        <strong>{b.place}</strong>
        <span className="vault-tags">
          <span className="chip st-gray">{t(`bookings.kind.${b.kind}`)}</span>
          <span className={BOOKING_STATUS_CHIP[b.status]}>{t(`bookings.status.${b.status}`)}</span>
        </span>
      </div>
      <div className="muted small">
        {[
          stay(b),
          b.reference && t("bookings.referenceValue", { reference: b.reference }),
          account ?? (b.vault_entry_id && t("bookings.accountInVault")),
        ]
          .filter(Boolean)
          .join(" · ")}
      </div>
      {b.deadline && (
        <div className="booking-line small">
          <span>
            {t(b.kind === "hotel" ? "bookings.freeCancelUntil" : "bookings.validUntil")}{" "}
            {formatDate(b.deadline, i18n.language)}
          </span>
          {live && <DeadlineChip days={b.days_left} />}
        </div>
      )}
      {b.warnings.map((w) => (
        <p key={w} className="alert alert-warn small">
          {t(`bookings.warning.${w}`)}
        </p>
      ))}
      {b.note && <p className="note-body small">{b.note}</p>}
      {(update.isError || remove.isError) && <ErrorAlert error={update.error ?? remove.error} />}
      <div className="note-actions">
        <button type="button" className="link-button" onClick={onEdit}>
          {t("reference.edit")}
        </button>
        {live && (
          <button
            type="button"
            className="link-button"
            disabled={update.isPending}
            onClick={() => update.mutate({ status: "cancelled" })}
          >
            {t("bookings.markCancelled")}
          </button>
        )}
        <button
          type="button"
          className="link-button"
          disabled={remove.isPending}
          onClick={() => {
            if (window.confirm(t("bookings.confirmDelete"))) remove.mutate();
          }}
        >
          {t("vault.delete")}
        </button>
      </div>
    </li>
  );
}

function BookingForm({
  caseId,
  booking,
  accounts,
  onDone,
}: {
  caseId: string;
  booking?: Booking;
  accounts: ReturnType<typeof useBookingAccounts>;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const refresh = useRefreshCards();
  const [kind, setKind] = useState<BookingKind>(booking?.kind ?? "hotel");
  const [status, setStatus] = useState<BookingStatus>(booking?.status ?? "active");
  const [place, setPlace] = useState(booking?.place ?? "");
  const [startsOn, setStartsOn] = useState(booking?.starts_on ?? "");
  const [endsOn, setEndsOn] = useState(booking?.ends_on ?? "");
  const [reference, setReference] = useState(booking?.reference ?? "");
  const [deadline, setDeadline] = useState(booking?.deadline ?? "");
  const [account, setAccount] = useState(booking?.vault_entry_id ?? "");
  const [note, setNote] = useState(booking?.note ?? "");
  const hotel = kind === "hotel";

  const save = useMutation({
    mutationFn: () => {
      const body = {
        kind,
        place: place.trim(),
        starts_on: startsOn || null,
        ends_on: endsOn || null,
        reference: reference.trim() || null,
        // One date per kind: the other is cleared.
        free_cancel_until: hotel ? deadline || null : null,
        valid_until: hotel ? null : deadline || null,
        // Without the vault the account is not shown, so it is left as it is.
        ...(accounts.enabled ? { vault_entry_id: account || null } : {}),
        note: note.trim() || null,
      };
      return booking
        ? apiPatch<Booking>(`/bookings/${booking.id}`, { ...body, status })
        : apiPost<Booking>(`/cases/${caseId}/bookings`, body);
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
    <form className="form-card booking-form" onSubmit={submit}>
      <div className="form-grid">
        <label className="field">
          <span>{t("bookings.kindLabel")}</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as BookingKind)}>
            {BOOKING_KINDS.map((k) => (
              <option key={k} value={k}>
                {t(`bookings.kind.${k}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t(hotel ? "bookings.hotel" : "bookings.flight")}</span>
          <input
            required
            maxLength={200}
            placeholder={t(hotel ? "bookings.hotelPlaceholder" : "bookings.flightPlaceholder")}
            value={place}
            onChange={(e) => setPlace(e.target.value)}
          />
        </label>
        <label className="field">
          <span>{t(hotel ? "bookings.checkIn" : "bookings.departure")}</span>
          <input type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} />
        </label>
        <label className="field">
          <span>{t(hotel ? "bookings.checkOut" : "bookings.return")}</span>
          <input
            type="date"
            min={startsOn || undefined}
            value={endsOn}
            onChange={(e) => setEndsOn(e.target.value)}
          />
        </label>
        <label className="field">
          <span>{t(hotel ? "bookings.freeCancelUntil" : "bookings.validUntil")}</span>
          <input type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("bookings.reference")}</span>
          <input maxLength={64} value={reference} onChange={(e) => setReference(e.target.value)} />
        </label>
        {accounts.enabled && (
          <label className="field">
            <span>{t("bookings.account")}</span>
            <select value={account} onChange={(e) => setAccount(e.target.value)}>
              <option value="">—</option>
              {accounts.options.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        )}
        {booking && (
          <label className="field">
            <span>{t("bookings.statusLabel")}</span>
            <select value={status} onChange={(e) => setStatus(e.target.value as BookingStatus)}>
              {BOOKING_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {t(`bookings.status.${s}`)}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <label className="field">
        <span>{t("vault.note")}</span>
        <input maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      {save.isError && <ErrorAlert error={save.error} />}
      <div className="form-actions">
        <button type="submit" className="btn btn-sm btn-p" disabled={save.isPending}>
          {t("common.save")}
        </button>
        <button type="button" className="btn btn-sm" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

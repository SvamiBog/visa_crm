import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { apiPatch, apiPost } from "../../api";
import {
  formatDate,
  type Attempt,
  type AttemptStatus,
  type QueueEntry,
  type QueueStatus,
  type VisaCase,
} from "../../contacts";
import { ErrorAlert, Row, todayIso } from "./ui";

// How a case gets its appointment (backlog 1.6): its number and email at the visa
// center, the waiting list or the applications with a window.

export const QUEUE_CHIP: Record<QueueStatus, string> = {
  in_queue: "chip st-orange",
  date_received: "chip st-green",
  submitted: "chip st-blue",
  removed: "chip st-gray",
};

export const ATTEMPT_CHIP: Record<AttemptStatus, string> = {
  open: "chip st-blue",
  date_received: "chip st-green",
  expired: "chip st-red",
  cancelled: "chip st-gray",
};

function useRefreshCards() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["contacts"] });
    void queryClient.invalidateQueries({ queryKey: ["queues"] });
  };
}

/** The name of the number for this country: ID, application number, form number… */
export function useNumberLabel() {
  const { t } = useTranslation();
  return (c: Pick<VisaCase, "identifier_type">) =>
    c.identifier_type ? t(`reference.identifier.${c.identifier_type}`) : t("queues.number");
}

export function Booking({ visaCase: c }: { visaCase: VisaCase }) {
  const { t } = useTranslation();
  const numberLabel = useNumberLabel();
  return (
    <>
      <Row label={numberLabel(c)}>{c.vac_number}</Row>
      <Row label={t("queues.email")}>
        {c.application_email &&
          [c.application_email, c.application_email_owner && t(`queues.owner.${c.application_email_owner}`)]
            .filter(Boolean)
            .join(" · ")}
      </Row>
      {c.booking_type === "waiting_list" && <WaitingList visaCase={c} />}
      {c.booking_type === "windowed_application" && <Attempts visaCase={c} />}
    </>
  );
}

/** A small inline form under the block with its own fields. */
function InlineForm({
  onSubmit,
  onCancel,
  pending,
  error,
  submitLabel,
  children,
}: {
  onSubmit: () => void;
  onCancel: () => void;
  pending: boolean;
  error: unknown;
  submitLabel: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <form
      className="form-card"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div className="form-grid">{children}</div>
      {error ? <ErrorAlert error={error} /> : null}
      <div className="form-actions">
        <button type="submit" className="btn btn-sm btn-p" disabled={pending}>
          {submitLabel}
        </button>
        <button type="button" className="btn btn-sm" onClick={onCancel}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

function DateField({
  label,
  value,
  onChange,
  required = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="date" required={required} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

// --- Waiting list --------------------------------------------------------------------

type QueueAction = "queue" | "letter" | "remove" | null;

function WaitingList({ visaCase: c }: { visaCase: VisaCase }) {
  const { t, i18n } = useTranslation();
  const refresh = useRefreshCards();
  const date = (value: string | null) => formatDate(value, i18n.language);
  const open = c.queue_entries.find((e) => !e.removed_on);
  const history = c.queue_entries.filter((e) => e.removed_on);
  const [action, setAction] = useState<QueueAction>(null);
  const [day, setDay] = useState(todayIso());
  const [appointment, setAppointment] = useState("");
  const [reason, setReason] = useState("");
  const [withGroup, setWithGroup] = useState(true);

  const start = (next: QueueAction) => {
    setDay(todayIso());
    setAppointment(c.submission_on ?? "");
    setReason("");
    setAction(next);
  };
  const done = () => {
    setAction(null);
    refresh();
  };
  const queue = useMutation({
    mutationFn: () =>
      apiPost<QueueEntry[]>(`/cases/${c.id}/queue`, {
        queued_on: day,
        with_group: Boolean(c.group_id) && withGroup,
      }),
    onSuccess: done,
  });
  const update = useMutation({
    mutationFn: (body: object) => apiPatch<QueueEntry>(`/queue-entries/${open?.id}`, body),
    onSuccess: done,
  });

  return (
    <div className="booking">
      <Row label={t("queues.waitingList")}>
        {open ? (
          <span className="stage-cell">
            <span className={QUEUE_CHIP[open.status]}>{t(`queues.status.${open.status}`)}</span>
            <span>
              {[
                t("queues.since", { date: date(open.queued_on) }),
                open.letter_on && t("queues.letterOn", { date: date(open.letter_on) }),
                t("queues.daysWaiting", { count: open.days_waiting }),
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </span>
        ) : (
          t("queues.notQueued")
        )}
      </Row>
      {c.average_wait_days !== null && (
        <p className="muted small">{t("queues.averageWait", { count: c.average_wait_days })}</p>
      )}
      {history.map((e) => (
        <p key={e.id} className="muted small">
          {t("queues.removedLine", {
            from: date(e.queued_on),
            to: date(e.removed_on),
            count: e.days_waiting,
          })}
          {e.removed_reason && ` · ${e.removed_reason}`}
        </p>
      ))}

      {action === "queue" && (
        <InlineForm
          onSubmit={() => queue.mutate()}
          onCancel={() => setAction(null)}
          pending={queue.isPending}
          error={queue.error}
          submitLabel={t("queues.putInQueue")}
        >
          <DateField label={t("queues.queuedOn")} value={day} onChange={setDay} required />
          {c.group_id && (
            <label className="checkbox">
              <input type="checkbox" checked={withGroup} onChange={(e) => setWithGroup(e.target.checked)} />
              <span>{t("queues.withGroup")}</span>
            </label>
          )}
        </InlineForm>
      )}
      {action === "letter" && (
        <InlineForm
          onSubmit={() => update.mutate({ letter_on: day, appointment_on: appointment || null })}
          onCancel={() => setAction(null)}
          pending={update.isPending}
          error={update.error}
          submitLabel={t("common.save")}
        >
          <DateField label={t("queues.letterDate")} value={day} onChange={setDay} required />
          <DateField label={t("cases.submissionOn")} value={appointment} onChange={setAppointment} />
        </InlineForm>
      )}
      {action === "remove" && (
        <InlineForm
          onSubmit={() => update.mutate({ removed_on: day, removed_reason: reason.trim() || null })}
          onCancel={() => setAction(null)}
          pending={update.isPending}
          error={update.error}
          submitLabel={t("queues.remove")}
        >
          <DateField label={t("queues.removedOn")} value={day} onChange={setDay} required />
          <label className="field">
            <span>{t("queues.removedReason")}</span>
            <input maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
        </InlineForm>
      )}
      {action === null && (
        <div className="note-actions">
          {!open && (
            <button type="button" className="link-button" onClick={() => start("queue")}>
              {t("queues.putInQueue")}
            </button>
          )}
          {open?.status === "in_queue" && (
            <button type="button" className="link-button" onClick={() => start("letter")}>
              {t("queues.dateCame")}
            </button>
          )}
          {open && (
            <button type="button" className="link-button" onClick={() => start("remove")}>
              {t("queues.remove")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// --- Applications with a window ------------------------------------------------------

type AttemptAction = "send" | "came" | null;

function Attempts({ visaCase: c }: { visaCase: VisaCase }) {
  const { t, i18n } = useTranslation();
  const refresh = useRefreshCards();
  const date = (value: string | null) => formatDate(value, i18n.language);
  const open = c.attempts.find((a) => a.status === "open");
  const [action, setAction] = useState<AttemptAction>(null);
  const [day, setDay] = useState(todayIso());
  const [appointment, setAppointment] = useState("");

  const done = () => {
    setAction(null);
    refresh();
  };
  const send = useMutation({
    mutationFn: () => apiPost<Attempt>(`/cases/${c.id}/attempts`, { sent_on: day }),
    onSuccess: done,
  });
  const update = useMutation({
    mutationFn: (body: object) => apiPatch<Attempt>(`/attempts/${open?.id}`, body),
    onSuccess: done,
  });

  return (
    <div className="booking">
      <Row label={t("queues.attempts", { days: c.window_days ?? "" })}>
        {c.attempts.length === 0 && t("queues.noAttempts")}
      </Row>
      {c.attempts.length > 0 && (
        <ul className="plain-list attempts">
          {c.attempts.map((a) => (
            <li key={a.id}>
              <span>
                {t("queues.attemptLine", {
                  number: a.number,
                  sent: date(a.sent_on),
                  deadline: date(a.deadline_on),
                })}
                {a.days_left !== null && (
                  <span className="muted"> · {t("queues.daysLeft", { count: a.days_left })}</span>
                )}
              </span>
              <span className={ATTEMPT_CHIP[a.status]}>{t(`queues.attempt.${a.status}`)}</span>
            </li>
          ))}
        </ul>
      )}
      {action === "send" && (
        <InlineForm
          onSubmit={() => send.mutate()}
          onCancel={() => setAction(null)}
          pending={send.isPending}
          error={send.error}
          submitLabel={t("queues.send")}
        >
          <DateField label={t("queues.sentOn")} value={day} onChange={setDay} required />
        </InlineForm>
      )}
      {action === "came" && (
        <InlineForm
          onSubmit={() => update.mutate({ outcome: "date_received", appointment_on: appointment || null })}
          onCancel={() => setAction(null)}
          pending={update.isPending}
          error={update.error}
          submitLabel={t("common.save")}
        >
          <DateField label={t("cases.submissionOn")} value={appointment} onChange={setAppointment} />
        </InlineForm>
      )}
      {action === null && (
        <div className="note-actions">
          {open ? (
            <>
              <button
                type="button"
                className="link-button"
                onClick={() => {
                  setAppointment(c.submission_on ?? "");
                  setAction("came");
                }}
              >
                {t("queues.dateCame")}
              </button>
              <button
                type="button"
                className="link-button"
                disabled={update.isPending}
                onClick={() => {
                  if (window.confirm(t("queues.confirmCancel"))) update.mutate({ outcome: "cancelled" });
                }}
              >
                {t("queues.cancelAttempt")}
              </button>
            </>
          ) : (
            <button
              type="button"
              className="link-button"
              onClick={() => {
                setDay(todayIso());
                setAction("send");
              }}
            >
              {t(c.attempts.length ? "queues.sendAgain" : "queues.send")}
            </button>
          )}
        </div>
      )}
      {update.isError && action === null && <ErrorAlert error={update.error} />}
    </div>
  );
}

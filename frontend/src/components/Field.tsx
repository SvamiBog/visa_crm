import { useEffect, useId, useRef, type ReactNode, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../api";

// A mistake in one field is shown under that field and tied to it (UI audit 2026-11-17,
// docs/decisions.md): the control is aria-invalid and described by the message, and after
// a failed submit the focus goes to the first such control, so a screen reader reads the
// field's name and its message. An error of no field stays in the form's alert.

/** What a form's control gets from its Field. */
export type ControlProps = {
  id: string;
  "aria-invalid"?: true;
  "aria-describedby"?: string;
};

/** A form field: the label, the control, the error under it, then the hint. The label is
 * its own element, so the message and the hint are not read as part of the field's name.
 * The error and the hint share one block: a field of a form grid has three rows (label,
 * control, notes), and a fourth child would land on the third one. */
export function Field({
  label,
  hint,
  error,
  className = "field",
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  className?: string;
  children: (control: ControlProps) => ReactNode;
}) {
  const id = useId();
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;
  // The message first: it is what the reader needs to hear.
  const described = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(" ");
  return (
    <div className={className}>
      <label htmlFor={id}>{label}</label>
      {children({
        id,
        "aria-invalid": error ? true : undefined,
        "aria-describedby": described || undefined,
      })}
      {(error || hint) && (
        <div className="field-notes">
          {error && (
            <small id={errorId} className="field-error small">
              {error}
            </small>
          )}
          {hint && (
            <small id={hintId} className="muted small">
              {hint}
            </small>
          )}
        </div>
      )}
    </div>
  );
}

/** A mistake the form finds before it asks the API. A new one on every submit, so the
 * focus goes back to its field each time. */
export class FormError extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

type Options = {
  // The API's codes (and the form's own) that belong to one field: code → field.
  codes?: Record<string, string>;
  // Fields the form shows a message under. The API's own validation (422) names the
  // field it turned down; a field the form does not show stays a general error.
  fields?: readonly string[];
  // A field's own text for the API's validation; errors.field_invalid otherwise.
  invalid?: Record<string, string>;
  // Values for the messages' placeholders, e.g. a password's minimum length.
  values?: Record<string, unknown>;
};

export type FieldErrors = {
  /** The form's ref: the focus goes to its first invalid control after a failed submit. */
  form: RefObject<HTMLFormElement>;
  /** The message under the field, if the error is its own. */
  at: (field: string) => string | undefined;
  /** The error that belongs to no field, for the form's alert; null otherwise. */
  general: unknown;
};

/** The fields the API's validation (422) turned down: FastAPI names each one by its path
 * in the body, e.g. ["body", "passport", "passport_number"] → "passport.passport_number". */
function rejectedFields(error: ApiError): string[] {
  const detail = error.data.detail;
  if (!Array.isArray(detail)) return [];
  return detail
    .map((item: { loc?: unknown }) => (Array.isArray(item?.loc) ? item.loc.slice(1).join(".") : ""))
    .filter(Boolean);
}

/** Which fields an error of a form belongs to and what to say under each. */
export function useFieldErrors(
  error: unknown,
  { codes = {}, fields = [], invalid = {}, values = {} }: Options = {},
): FieldErrors {
  const { t } = useTranslation();
  const form = useRef<HTMLFormElement>(null);
  const messages = new Map<string, string>();
  const code = error instanceof ApiError || error instanceof FormError ? error.code : null;
  if (code && codes[code]) {
    messages.set(codes[code], t(`errors.${code}`, values));
  } else if (error instanceof ApiError && code === "validation") {
    for (const field of rejectedFields(error).filter((f) => fields.includes(f))) {
      messages.set(field, t(`errors.${invalid[field] ?? "field_invalid"}`, values));
    }
  }
  const tied = messages.size > 0;

  useEffect(() => {
    if (tied) form.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [error, tied]);

  return {
    form,
    at: (field) => messages.get(field),
    general: tied ? null : error,
  };
}

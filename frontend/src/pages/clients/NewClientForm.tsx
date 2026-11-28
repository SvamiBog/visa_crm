import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ApiError, apiPost } from "../../api";
import { useDisclosure } from "../../components/Disclosure";
import { Field, useFieldErrors } from "../../components/Field";
import type { Contact, Passport } from "../../contacts";
import {
  AnswersInputs,
  BLANK_ANSWERS,
  ConsultationInputs,
  answerFields,
  answersBody,
  consultationBody,
  consultationDraft,
  consultationFields,
  useMoneySettings,
  type Answers,
} from "./Consultations";
import {
  CHECKED_FIELDS,
  ContactInputs,
  PaperworkInputs,
  PassportInputs,
  initial,
  initialPassport,
  nullable,
  type Fields,
} from "./ContactForm";
import { ErrorAlert } from "./ui";

// A new client in one go (backlog 1.15.16): the card is opened on the first conversation,
// and what the person tells then goes in with it — the questionnaire, the consultation and
// a note. The passport, the consent and the contract wait folded. One button sends it all
// to POST /contacts, which keeps it in one transaction: a mistake anywhere saves nothing.

// Fields of the folded part «Паспорт, согласие и договор», by their path in the body.
const PAPERWORK_FIELDS = new Set<string>([
  "last_name_latin",
  "first_name_latin",
  "passport.passport_number",
  "passport.personal_number",
  "passport.passport_issued_on",
  "passport.passport_expires_on",
  "passport.passport_issued_by",
  "passport.birth_place",
  "consent_signed_on",
  "contract_number",
  "contract_signed_on",
]);

// Fields of the consultation's folded part: the decision, when to call, the trip, the fee.
const MORE_FIELDS = new Set<string>([
  "consultation.follow_up_on",
  "consultation.trip_planned_on",
  "consultation.fee_amount",
  "consultation.fee_currency",
  "consultation.fee_paid_on",
]);

export function NewClientForm({
  onSaved,
  onCancel,
}: {
  onSaved: (contact: Contact) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const money = useMoneySettings();
  const [fields, setFields] = useState<Fields>(() => initial());
  const [passport, setPassportFields] = useState(() => initialPassport());
  const [answers, setAnswers] = useState<Answers>(BLANK_ANSWERS);
  // The date of today and one applicant stand by themselves: a block left at them is not
  // a consultation. Its channel is the contact's, which the API puts in.
  const [pristine] = useState(() => consultationDraft());
  const [consultation, setConsultation] = useState(pristine);
  const [note, setNote] = useState("");
  const more = useDisclosure();
  const papers = useDisclosure();
  const set = (key: keyof Fields) => (value: string) =>
    setFields((current) => ({ ...current, [key]: value }));
  const setPassport = (key: keyof Passport) => (value: string) =>
    setPassportFields((current) => ({ ...current, [key]: value }));
  const named = Boolean(fields.last_name.trim() || fields.first_name.trim());
  // A date or a number of applicants wiped out counts as left as it was: the block stays
  // empty instead of stopping the form on a required field nobody meant to fill.
  const consulted =
    JSON.stringify({
      ...consultation,
      held_on: consultation.held_on || pristine.held_on,
      applicants: consultation.applicants || pristine.applicants,
    }) !== JSON.stringify(pristine);

  // A nick another card has is only a warning: «Всё равно сохранить» sends the whole form.
  const save = useMutation({
    mutationFn: (allowSharedTelegram: boolean) => {
      const body = {
        ...nullable(fields),
        passport: nullable(passport),
        questionnaire: answersBody(answers),
        consultation: consulted
          ? consultationBody(consultation, money.data?.base_currency)
          : null,
        note: note.trim() || null,
      };
      const query = allowSharedTelegram ? "?allow_shared_telegram=true" : "";
      return apiPost<Contact>(`/contacts${query}`, body);
    },
    onSuccess: onSaved,
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(false);
  };

  const errors = useFieldErrors(save.error, {
    codes: {
      phone_taken: "phone",
      telegram_taken: "telegram",
      name_required: "last_name",
      currency_not_accepted: "consultation.fee_currency",
    },
    fields: [
      ...CHECKED_FIELDS,
      ...answerFields(answers, "questionnaire."),
      ...consultationFields("consultation."),
      "note",
    ],
    invalid: {
      phone: "phone_invalid",
      "passport.passport_number": "document_number_invalid",
      "passport.personal_number": "document_number_invalid",
    },
  });

  // A folded part with a mistake in it opens, and the focus goes to that field once it
  // shows (useFieldErrors cannot focus a hidden one).
  const refocus = useRef(false);
  useEffect(() => {
    const wrong = [...PAPERWORK_FIELDS, ...MORE_FIELDS].filter((field) => errors.at(field));
    if (wrong.some((field) => PAPERWORK_FIELDS.has(field)) && !papers.open) {
      refocus.current = true;
      papers.show();
    }
    if (wrong.some((field) => MORE_FIELDS.has(field)) && !more.open) {
      refocus.current = true;
      more.show();
    }
    // Only a new error opens a part: the user may fold it again.
  }, [save.error]);
  // The browser's own check (a passport number with a dash) stops the submit on a field
  // it cannot show while its part is folded: the part opens and the browser says it there.
  type Control = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
  const unshown = useRef<Control | null>(null);
  const invalid = (event: FormEvent<HTMLFormElement>) => {
    const control = event.target as Control;
    // Only the form's first mistake: a field shown above it keeps the browser's focus.
    if (errors.form.current?.querySelector(":invalid") !== control) return;
    for (const part of [papers, more]) {
      if (!part.open && document.getElementById(part.panel.id)?.contains(control)) {
        unshown.current ??= control;
        part.show();
      }
    }
  };
  useEffect(() => {
    if (unshown.current) {
      unshown.current.focus();
      unshown.current.reportValidity();
      unshown.current = null;
    } else if (refocus.current) {
      errors.form.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
    }
    refocus.current = false;
  }, [papers.open, more.open, errors.form]);

  const taken =
    save.error instanceof ApiError &&
    (save.error.code === "phone_taken" || save.error.code === "telegram_taken")
      ? save.error
      : null;
  // An employee is told the phone is taken without a link to a card they do not see.
  const duplicate = taken ? String(taken.data.contact_id ?? "") : null;
  const openExisting = duplicate && (
    <>
      {" "}
      <Link to={`/app/clients/${duplicate}`}>{t("contacts.openExisting")}</Link>
    </>
  );
  const phoneError = errors.at("phone") && (
    <>
      {errors.at("phone")}
      {taken?.code === "phone_taken" && openExisting}
    </>
  );
  const telegramError = errors.at("telegram") && (
    <>
      {errors.at("telegram")}
      {taken?.code === "telegram_taken" && (
        <>
          {openExisting}{" "}
          <button
            type="button"
            className="btn btn-sm"
            disabled={save.isPending}
            onClick={() => save.mutate(true)}
          >
            {t("contacts.saveAnyway")}
          </button>
        </>
      )}
    </>
  );
  const prefixed = (prefix: string) => (field: string) => errors.at(prefix + field);

  return (
    <form
      ref={errors.form}
      className="card form-card"
      onSubmit={submit}
      onInvalidCapture={invalid}
    >
      <h2 className="h2">{t("contacts.newTitle")}</h2>

      <section className="form-section">
        <h3 className="h3">{t("newClient.contact")}</h3>
        <ContactInputs
          fields={fields}
          set={set}
          at={errors.at}
          phoneError={phoneError}
          telegramError={telegramError}
          autoFocus
        />
      </section>

      <section className="form-section">
        <h3 className="h3">{t("contacts.questionnaire")}</h3>
        <AnswersInputs
          value={answers}
          onChange={setAnswers}
          at={prefixed("questionnaire.")}
          paired
        />
      </section>

      <section className="form-section">
        <h3 className="h3">{t("newClient.consultation")}</h3>
        <p className="muted small">{t("newClient.consultationHint")}</p>
        <ConsultationInputs
          value={consultation}
          onChange={setConsultation}
          at={prefixed("consultation.")}
          more={more}
        />
      </section>

      <section className="form-section">
        <h3 className="h3">{t("newClient.note")}</h3>
        <Field
          label={<span className="visually-hidden">{t("contacts.newNote")}</span>}
          error={errors.at("note")}
        >
          {(control) => (
            <textarea
              {...control}
              rows={3}
              maxLength={5000}
              placeholder={t("contacts.notePlaceholder")}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          )}
        </Field>
      </section>

      <section className="form-section">
        <h3 className="h3">
          <button className="disclosure-title" {...papers.button}>
            {t("newClient.paperwork")}
          </button>
        </h3>
        <p className="muted small">{t("contacts.passportFormHint")}</p>
        <div className="form-card" {...papers.panel}>
          <h4 className="h3">{t("contacts.passport")}</h4>
          <PassportInputs
            fields={fields}
            set={set}
            document={passport}
            setPassport={setPassport}
            at={errors.at}
          />
          <h4 className="h3">{t("contacts.paperwork")}</h4>
          <PaperworkInputs fields={fields} set={set} at={errors.at} />
        </div>
      </section>

      {errors.general ? <ErrorAlert error={errors.general} /> : null}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={save.isPending || !named}>
          {t("contacts.create")}
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

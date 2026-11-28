import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ApiError, apiPatch, apiPost } from "../../api";
import { Field, useFieldErrors } from "../../components/Field";
import {
  MESSENGERS,
  PASSPORT_FIELDS,
  SEXES,
  passportOf,
  refreshAfterPassportPurge,
  useContactLists,
  type Contact,
  type Messenger,
  type Passport,
} from "../../contacts";
import { useLocalName } from "../../reference";
import { ErrorAlert, useSortedCountries } from "./ui";

type Fields = {
  last_name: string;
  first_name: string;
  middle_name: string;
  last_name_latin: string;
  first_name_latin: string;
  phone: string;
  telegram: string;
  messenger: Messenger | "";
  messenger_contact: string;
  email: string;
  birth_date: string;
  lead_channel_id: string;
  partner_id: string;
  consent_signed_on: string;
  contract_number: string;
  contract_signed_on: string;
};

type PassportFields = Record<keyof Passport, string>;

// The text fields that show the API's validation under them, by their path in the body.
const CHECKED_FIELDS = [
  "last_name",
  "first_name",
  "middle_name",
  "phone",
  "telegram",
  "messenger_contact",
  "email",
  "birth_date",
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
] as const;

function initial(contact?: Contact): Fields {
  return {
    last_name: contact?.last_name ?? "",
    first_name: contact?.first_name ?? "",
    middle_name: contact?.middle_name ?? "",
    last_name_latin: contact?.last_name_latin ?? "",
    first_name_latin: contact?.first_name_latin ?? "",
    phone: contact?.phone ?? "",
    telegram: contact?.telegram ?? "",
    messenger: contact?.messenger ?? "",
    messenger_contact: contact?.messenger_contact ?? "",
    email: contact?.email ?? "",
    birth_date: contact?.birth_date ?? "",
    lead_channel_id: contact?.lead_channel_id ?? "",
    partner_id: contact?.partner_id ?? "",
    consent_signed_on: contact?.consent_signed_on ?? "",
    contract_number: contact?.contract_number ?? "",
    contract_signed_on: contact?.contract_signed_on ?? "",
  };
}

function initialPassport(passport?: Passport): PassportFields {
  const current = passport ? passportOf(passport) : undefined;
  return Object.fromEntries(
    PASSPORT_FIELDS.map((key) => [key, current?.[key] ?? ""]),
  ) as PassportFields;
}

/** Empty inputs are sent as null: the API clears the field. */
function nullable(fields: Record<string, string>) {
  const body: Record<string, string | null> = {};
  for (const [key, value] of Object.entries(fields)) {
    body[key] = value.trim() === "" ? null : value.trim();
  }
  return body;
}

/** New contact (no `contact`) or changes to an existing one, with the passport. */
export function ContactForm({
  contact,
  passport,
  onSaved,
  onCancel,
}: {
  contact?: Contact;
  // The card's questionnaire: the passport is kept there.
  passport?: Passport;
  onSaved: (contact: Contact) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const lists = useContactLists();
  const countries = useSortedCountries();
  const name = useLocalName();
  const [fields, setFields] = useState<Fields>(() => initial(contact));
  const [document, setDocument] = useState<PassportFields>(() => initialPassport(passport));
  // The card the form was opened from: if its passport is removed meanwhile, the API does
  // not take the old values back from here (backlog A11).
  const [openedAt] = useState(contact?.updated_at);
  const queryClient = useQueryClient();
  const set = (key: keyof Fields) => (value: string) =>
    setFields((current) => ({ ...current, [key]: value }));
  const setPassport = (key: keyof Passport) => (value: string) =>
    setDocument((current) => ({ ...current, [key]: value }));
  const named = Boolean(fields.last_name.trim() || fields.first_name.trim());

  // A nick another card has is only a warning: the specialist may save anyway.
  const save = useMutation({
    mutationFn: (allowSharedTelegram: boolean) => {
      const body = { ...nullable(fields), passport: nullable(document) };
      const params = new URLSearchParams();
      if (allowSharedTelegram) params.set("allow_shared_telegram", "true");
      if (contact && openedAt) params.set("card_updated_at", openedAt);
      const query = params.toString() ? `?${params}` : "";
      return contact
        ? apiPatch<Contact>(`/contacts/${contact.id}${query}`, body)
        : apiPost<Contact>(`/contacts${query}`, body);
    },
    onSuccess: onSaved,
    // Removed while the form was open: the card behind it shows the passport as it is now,
    // and the form opened again starts from it.
    onError: (error) => {
      if (contact && error instanceof ApiError && error.code === "passport_changed")
        void refreshAfterPassportPurge(queryClient, contact.id);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(false);
  };

  const errors = useFieldErrors(save.error, {
    codes: { phone_taken: "phone", telegram_taken: "telegram", name_required: "last_name" },
    fields: CHECKED_FIELDS,
    invalid: { phone: "phone_invalid" },
  });
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
  // A nick another card has: that card, or saving anyway.
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
  const activeOr = <T extends { id: string; is_active: boolean }>(items: T[], current: string) =>
    items.filter((item) => item.is_active || item.id === current);

  return (
    <form ref={errors.form} className="card form-card" onSubmit={submit}>
      <h2 className="h2">{contact ? t("contacts.editTitle") : t("contacts.newTitle")}</h2>
      <div className="form-grid">
        <TextField label={t("contacts.lastName")} value={fields.last_name} error={errors.at("last_name")} onChange={set("last_name")} maxLength={100} autoFocus />
        <TextField label={t("contacts.firstName")} value={fields.first_name} error={errors.at("first_name")} onChange={set("first_name")} maxLength={100} hint={t("contacts.nameHint")} />
        <TextField label={t("contacts.middleName")} value={fields.middle_name} error={errors.at("middle_name")} onChange={set("middle_name")} maxLength={100} />
        <TextField label={t("contacts.phone")} value={fields.phone} error={phoneError} onChange={set("phone")} type="tel" hint={t("contacts.phoneHint")} />
        <TextField label={t("contacts.telegram")} value={fields.telegram} error={telegramError} onChange={set("telegram")} hint={t("contacts.telegramHint")} />
        <label className="field">
          <span>{t("contacts.messenger")}</span>
          <select value={fields.messenger} onChange={(e) => set("messenger")(e.target.value)}>
            <option value="">{t("contacts.noMessenger")}</option>
            {MESSENGERS.map((m) => (
              <option key={m} value={m}>
                {t(`contacts.messengers.${m}`)}
              </option>
            ))}
          </select>
        </label>
        {fields.messenger && (
          <TextField label={t("contacts.messengerContact")} value={fields.messenger_contact} error={errors.at("messenger_contact")} onChange={set("messenger_contact")} />
        )}
        <TextField label={t("auth.email")} value={fields.email} error={errors.at("email")} onChange={set("email")} type="email" />
        <TextField label={t("contacts.birthDate")} value={fields.birth_date} error={errors.at("birth_date")} onChange={set("birth_date")} type="date" />
        <label className="field">
          <span>{t("contacts.channel")}</span>
          <select value={fields.lead_channel_id} onChange={(e) => set("lead_channel_id")(e.target.value)}>
            <option value="" />
            {activeOr(lists.channels, fields.lead_channel_id).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("contacts.partner")}</span>
          <select value={fields.partner_id} onChange={(e) => set("partner_id")(e.target.value)}>
            <option value="" />
            {activeOr(lists.partners, fields.partner_id).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <h3 className="h3">{t("contacts.passport")}</h3>
      <p className="muted small">{t("contacts.passportFormHint")}</p>
      <div className="form-grid">
        <TextField label={t("contacts.lastNameLatin")} value={fields.last_name_latin} error={errors.at("last_name_latin")} onChange={set("last_name_latin")} maxLength={100} />
        <TextField label={t("contacts.firstNameLatin")} value={fields.first_name_latin} error={errors.at("first_name_latin")} onChange={set("first_name_latin")} maxLength={100} />
        <TextField label={t("contacts.passportNumber")} value={document.passport_number} error={errors.at("passport.passport_number")} onChange={setPassport("passport_number")} maxLength={20} pattern="[A-Za-z0-9 ]*" />
        <TextField label={t("contacts.personalNumber")} value={document.personal_number} error={errors.at("passport.personal_number")} onChange={setPassport("personal_number")} maxLength={20} pattern="[A-Za-z0-9 ]*" />
        <label className="field">
          <span>{t("contacts.nationality")}</span>
          <select value={document.nationality} onChange={(e) => setPassport("nationality")(e.target.value)}>
            <option value="" />
            {countries.map((c) => (
              <option key={c.id} value={c.code}>
                {name.country(c)}
              </option>
            ))}
            {document.nationality && !countries.some((c) => c.code === document.nationality) && (
              <option value={document.nationality}>{document.nationality}</option>
            )}
          </select>
        </label>
        <label className="field">
          <span>{t("contacts.sex")}</span>
          <select value={document.sex} onChange={(e) => setPassport("sex")(e.target.value)}>
            <option value="" />
            {SEXES.map((value) => (
              <option key={value} value={value}>
                {t(`contacts.sexValue.${value}`)}
              </option>
            ))}
          </select>
        </label>
        <TextField label={t("contacts.passportIssuedOn")} value={document.passport_issued_on} error={errors.at("passport.passport_issued_on")} onChange={setPassport("passport_issued_on")} type="date" />
        <TextField label={t("contacts.passportExpiresOn")} value={document.passport_expires_on} error={errors.at("passport.passport_expires_on")} onChange={setPassport("passport_expires_on")} type="date" />
        <TextField label={t("contacts.passportIssuedBy")} value={document.passport_issued_by} error={errors.at("passport.passport_issued_by")} onChange={setPassport("passport_issued_by")} maxLength={200} />
        <TextField label={t("contacts.birthPlace")} value={document.birth_place} error={errors.at("passport.birth_place")} onChange={setPassport("birth_place")} maxLength={200} />
      </div>

      <h3 className="h3">{t("contacts.paperwork")}</h3>
      <div className="form-grid">
        <TextField label={t("contacts.consentSignedOn")} value={fields.consent_signed_on} error={errors.at("consent_signed_on")} onChange={set("consent_signed_on")} type="date" />
        <TextField label={t("contacts.contractNumber")} value={fields.contract_number} error={errors.at("contract_number")} onChange={set("contract_number")} />
        <TextField label={t("contacts.contractSignedOn")} value={fields.contract_signed_on} error={errors.at("contract_signed_on")} onChange={set("contract_signed_on")} type="date" />
      </div>

      {errors.general ? <ErrorAlert error={errors.general} /> : null}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={save.isPending || !named}>
          {contact ? t("common.save") : t("contacts.create")}
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

function TextField({
  label,
  value,
  onChange,
  type = "text",
  hint,
  error,
  maxLength,
  pattern,
  autoFocus,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  hint?: string;
  error?: ReactNode;
  maxLength?: number;
  pattern?: string;
  autoFocus?: boolean;
}) {
  return (
    <Field label={label} hint={hint} error={error}>
      {(control) => (
        <input
          {...control}
          type={type}
          value={value}
          maxLength={maxLength}
          pattern={pattern}
          autoFocus={autoFocus}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </Field>
  );
}

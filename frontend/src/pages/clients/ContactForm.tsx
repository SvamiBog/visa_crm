import { useMutation } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ApiError, apiPatch, apiPost, errorCode } from "../../api";
import { MESSENGERS, useContactLists, type Contact, type Messenger } from "../../contacts";

type Fields = {
  full_name: string;
  full_name_latin: string;
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

function initial(contact?: Contact): Fields {
  return {
    full_name: contact?.full_name ?? "",
    full_name_latin: contact?.full_name_latin ?? "",
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

/** Empty inputs are sent as null: the API clears the field. */
function toBody(fields: Fields) {
  const body: Record<string, string | null> = {};
  for (const [key, value] of Object.entries(fields)) {
    body[key] = value.trim() === "" ? null : value.trim();
  }
  return body;
}

/** New contact (no `contact`) or changes to an existing one. */
export function ContactForm({
  contact,
  onSaved,
  onCancel,
}: {
  contact?: Contact;
  onSaved: (contact: Contact) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const lists = useContactLists();
  const [fields, setFields] = useState<Fields>(() => initial(contact));
  const set = (key: keyof Fields) => (value: string) =>
    setFields((current) => ({ ...current, [key]: value }));

  const save = useMutation({
    mutationFn: () =>
      contact
        ? apiPatch<Contact>(`/contacts/${contact.id}`, toBody(fields))
        : apiPost<Contact>("/contacts", toBody(fields)),
    onSuccess: onSaved,
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  const duplicate =
    save.error instanceof ApiError && save.error.code === "phone_taken"
      ? String(save.error.data.contact_id ?? "")
      : null;
  const activeOr = <T extends { id: string; is_active: boolean }>(items: T[], current: string) =>
    items.filter((item) => item.is_active || item.id === current);

  return (
    <form className="card form-card" onSubmit={submit}>
      <h2 className="h2">{contact ? t("contacts.editTitle") : t("contacts.newTitle")}</h2>
      <div className="form-grid">
        <TextField label={t("contacts.fullName")} value={fields.full_name} onChange={set("full_name")} required autoFocus />
        <TextField label={t("contacts.fullNameLatin")} value={fields.full_name_latin} onChange={set("full_name_latin")} hint={t("contacts.fullNameLatinHint")} />
        <TextField label={t("contacts.phone")} value={fields.phone} onChange={set("phone")} type="tel" hint={t("contacts.phoneHint")} />
        <TextField label={t("contacts.telegram")} value={fields.telegram} onChange={set("telegram")} hint={t("contacts.telegramHint")} />
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
          <TextField label={t("contacts.messengerContact")} value={fields.messenger_contact} onChange={set("messenger_contact")} />
        )}
        <TextField label={t("auth.email")} value={fields.email} onChange={set("email")} type="email" />
        <TextField label={t("contacts.birthDate")} value={fields.birth_date} onChange={set("birth_date")} type="date" />
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

      <h3 className="h3">{t("contacts.paperwork")}</h3>
      <div className="form-grid">
        <TextField label={t("contacts.consentSignedOn")} value={fields.consent_signed_on} onChange={set("consent_signed_on")} type="date" />
        <TextField label={t("contacts.contractNumber")} value={fields.contract_number} onChange={set("contract_number")} />
        <TextField label={t("contacts.contractSignedOn")} value={fields.contract_signed_on} onChange={set("contract_signed_on")} type="date" />
      </div>

      {save.isError && (
        <div className="alert alert-error" role="alert">
          {duplicate ? (
            <>
              {t("errors.phone_taken")}{" "}
              <Link to={`/app/clients/${duplicate}`}>{t("contacts.openExisting")}</Link>
            </>
          ) : (
            t(`errors.${errorCode(save.error)}`, { defaultValue: t("errors.unknown") })
          )}
        </div>
      )}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={save.isPending || !fields.full_name.trim()}>
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
  required,
  autoFocus,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  hint?: string;
  required?: boolean;
  autoFocus?: boolean;
}) {
  const id = useId();
  // The hint stays outside the label so it is not read as part of the field's name.
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type={type}
        value={value}
        required={required}
        autoFocus={autoFocus}
        aria-describedby={hint ? `${id}-hint` : undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      {hint && (
        <small id={`${id}-hint`} className="muted small">
          {hint}
        </small>
      )}
    </div>
  );
}

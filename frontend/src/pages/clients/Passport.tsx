import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type ChangeEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { apiPatch, apiPost, apiPut } from "../../api";
import { contactKey, formatDate, type ContactCard, type Questionnaire } from "../../contacts";
import { PhotoUnreadable, readZone } from "../../ocr";
import { useCountries, useLocalName } from "../../reference";
import { ErrorAlert } from "./ui";

// «Распознать паспорт» (backlog 2.5): the machine-readable zone from a photo, read in
// the browser, or typed in; the API checks it; the specialist picks what goes into the
// card. The photo is not uploaded and not kept anywhere.

export type MrzReading = {
  format: string;
  document_type: string;
  issuing_state: string;
  issuing_country: string | null;
  surname: string;
  given_names: string;
  full_name_latin: string;
  document_number: string;
  nationality: string;
  nationality_country: string | null;
  birth_date: string | null;
  sex: "M" | "F" | "X" | null;
  expiry_date: string | null;
  expired: boolean;
  personal_number: string | null;
  lines: string[];
  valid: boolean;
  failed: string[];
  corrected: string[];
};

type Field = {
  key: string;
  label: string;
  value: string | null;
  shown: ReactNode;
  current: ReactNode;
};

export function PassportReader({ card, onDone }: { card: ContactCard; onDone: () => void }) {
  const { t } = useTranslation();
  const [lines, setLines] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [photoError, setPhotoError] = useState<"notFound" | "photoUnreadable" | null>(null);
  const read = useMutation({
    mutationFn: (text: string) => apiPost<MrzReading>("/mrz/read", { text }),
  });

  const choose = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = ""; // the same photo can be chosen again
    if (!file) return;
    setPhotoError(null);
    read.reset();
    setProgress(0);
    try {
      const text = await readZone(file, setProgress);
      if (text) {
        setLines(text);
        read.mutate(text);
      } else {
        setPhotoError("notFound");
      }
    } catch (error) {
      setPhotoError(error instanceof PhotoUnreadable ? "photoUnreadable" : "notFound");
    } finally {
      setProgress(null);
    }
  };

  return (
    <div className="form-card passport-reader">
      <h3 className="h3">{t("mrz.title")}</h3>
      <p className="muted small">{t("mrz.hint")}</p>
      <label className="field">
        <span>{t("mrz.photo")}</span>
        <input type="file" accept="image/*" disabled={progress !== null} onChange={choose} />
      </label>
      {progress !== null && (
        <p className="muted small" role="status">
          {t("mrz.reading", { percent: Math.round(progress * 100) })}
        </p>
      )}
      {photoError && <p className="alert alert-warn small">{t(`mrz.${photoError}`)}</p>}
      <label className="field">
        <span>{t("mrz.lines")}</span>
        <textarea
          className="mono mrz-lines"
          rows={3}
          spellCheck={false}
          value={lines}
          onChange={(e) => setLines(e.target.value)}
        />
        <small className="muted">{t("mrz.linesHint")}</small>
      </label>
      <div className="form-actions">
        <button
          type="button"
          className="btn btn-sm"
          disabled={!lines.trim() || read.isPending}
          onClick={() => read.mutate(lines)}
        >
          {t("mrz.check")}
        </button>
        <button type="button" className="btn btn-sm" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
      {read.isError && <ErrorAlert error={read.error} />}
      {read.data && <Reading key={read.data.lines.join()} card={card} mrz={read.data} onDone={onDone} />}
    </div>
  );
}

function Reading({
  card,
  mrz,
  onDone,
}: {
  card: ContactCard;
  mrz: MrzReading;
  onDone: () => void;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const name = useLocalName();
  const countries = useCountries();
  const country = (code: string | null) =>
    code ? name.country(countries.data?.find((c) => c.code === code)) || code : "";
  const date = (value: string | null) => formatDate(value, i18n.language);
  const sex = (value: string | null) => (value ? t(`contacts.sexValue.${value}`) : "");
  const q = card.questionnaire;
  const c = card.contact;

  const fields: Field[] = [
      {
        key: "full_name_latin",
        label: t("contacts.fullNameLatin"),
        value: mrz.full_name_latin || null,
        shown: mrz.full_name_latin,
        current: c.full_name_latin,
      },
      {
        key: "birth_date",
        label: t("contacts.birthDate"),
        value: mrz.birth_date,
        shown: date(mrz.birth_date),
        current: date(c.birth_date),
      },
      {
        key: "passport_number",
        label: t("contacts.passportNumber"),
        value: mrz.document_number || null,
        shown: mrz.document_number,
        current: q.passport_number,
      },
      {
        key: "passport_expires_on",
        label: t("contacts.passportExpiresOn"),
        value: mrz.expiry_date,
        shown: date(mrz.expiry_date),
        current: date(q.passport_expires_on),
      },
      {
        key: "nationality",
        label: t("contacts.nationality"),
        value: mrz.nationality_country,
        shown: country(mrz.nationality_country) || mrz.nationality,
        current: country(q.nationality),
      },
      {
        key: "sex",
        label: t("contacts.sex"),
        value: mrz.sex,
        shown: sex(mrz.sex),
        current: sex(q.sex),
      },
      {
        key: "personal_number",
        label: t("contacts.personalNumber"),
        value: mrz.personal_number,
        shown: mrz.personal_number,
        current: q.personal_number,
      },
  ];
  // What differs from the card is ticked; a reading with failed checks is left to the
  // specialist to tick after comparing with the passport.
  const [chosen, setChosen] = useState<Set<string>>(
    () =>
      new Set(
        mrz.valid
          ? fields.filter((f) => f.value !== null && f.shown !== f.current).map((f) => f.key)
          : [],
      ),
  );
  const toggle = (key: string) =>
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const apply = useMutation({
    mutationFn: async () => {
      const picked = Object.fromEntries(
        fields.filter((f) => chosen.has(f.key) && f.value !== null).map((f) => [f.key, f.value]),
      );
      const contact = ["full_name_latin", "birth_date"].filter((k) => k in picked);
      if (contact.length > 0) {
        await apiPatch(`/contacts/${c.id}`, Object.fromEntries(contact.map((k) => [k, picked[k]])));
      }
      const passport = Object.keys(picked).filter((k) => !contact.includes(k));
      if (passport.length > 0) {
        const next: Questionnaire = { ...q };
        for (const key of passport) Object.assign(next, { [key]: picked[key] });
        await apiPut(`/contacts/${c.id}/questionnaire`, next);
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: contactKey(c.id) });
      onDone();
    },
  });

  return (
    <>
      {mrz.valid ? (
        <p className="alert alert-ok small">{t("mrz.valid")}</p>
      ) : (
        <p className="alert alert-error small">
          {t("mrz.invalid", {
            fields: mrz.failed.map((f) => t(`mrz.field.${f}`)).join(", "),
          })}
        </p>
      )}
      {mrz.corrected.length > 0 && <p className="alert alert-warn small">{t("mrz.corrected")}</p>}
      {mrz.expired && <p className="alert alert-warn small">{t("mrz.expired")}</p>}
      <div className="table-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th aria-label={t("mrz.col.take")} />
              <th>{t("mrz.col.field")}</th>
              <th>{t("mrz.col.value")}</th>
              <th>{t("mrz.col.current")}</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f) => (
              <tr key={f.key}>
                <td>
                  <input
                    type="checkbox"
                    aria-label={f.label}
                    disabled={f.value === null}
                    checked={chosen.has(f.key)}
                    onChange={() => toggle(f.key)}
                  />
                </td>
                <td className="muted">{f.label}</td>
                <td>{f.shown || "—"}</td>
                <td className="muted">{f.current || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {apply.isError && <ErrorAlert error={apply.error} />}
      <div className="form-actions">
        <button
          type="button"
          className="btn btn-sm btn-p"
          disabled={chosen.size === 0 || apply.isPending}
          onClick={() => apply.mutate()}
        >
          {t("mrz.apply")}
        </button>
      </div>
    </>
  );
}

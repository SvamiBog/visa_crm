import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type ChangeEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { apiPatch, apiPost } from "../../api";
import { PASSPORT_FIELDS, contactKey, formatDate, type ContactCard } from "../../contacts";
import { PhotoUnreadable, readZones } from "../../ocr";
import { useCountries, useLocalName } from "../../reference";
import { useHasModule } from "../../workspace";
import { ErrorAlert, Row } from "./ui";

// The passport on the client's card: the Latin name and the data page, entered with the
// card's form (ContactForm) or read from the passport.

export function PassportCard({ card }: { card: ContactCard }) {
  const { t, i18n } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const [reading, setReading] = useState(false);
  // Only the filled rows show; the empty ones come with «Показать все поля».
  const [allFields, setAllFields] = useState(false);
  const rowsId = useId();
  const canRead = useHasModule("passport_mrz");
  const q = card.questionnaire;
  const c = card.contact;
  const date = (value: string | null) => formatDate(value, i18n.language);
  const filled = Boolean(c.full_name_latin) || PASSPORT_FIELDS.some((key) => q[key]);
  const rows: [string, ReactNode][] = [
    [t("contacts.lastNameLatin"), c.last_name_latin],
    [t("contacts.firstNameLatin"), c.first_name_latin],
    [t("contacts.passportNumber"), q.passport_number],
    [t("contacts.personalNumber"), q.personal_number],
    [
      t("contacts.nationality"),
      q.nationality &&
        (name.country(countries.data?.find((x) => x.code === q.nationality)) || q.nationality),
    ],
    [t("contacts.sex"), q.sex && t(`contacts.sexValue.${q.sex}`)],
    [t("contacts.passportIssuedOn"), date(q.passport_issued_on)],
    [t("contacts.passportExpiresOn"), date(q.passport_expires_on)],
    [t("contacts.passportIssuedBy"), q.passport_issued_by],
    [t("contacts.birthPlace"), q.birth_place],
  ];

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="h2">{t("contacts.passport")}</h2>
        {canRead && !reading && (
          <div className="actions">
            <button type="button" className="btn btn-sm" onClick={() => setReading(true)}>
              {t("mrz.open")}
            </button>
          </div>
        )}
      </div>
      {reading && <PassportReader card={card} onDone={() => setReading(false)} />}
      {filled ? (
        <>
          <div id={rowsId}>
            {rows
              .filter(([, value]) => allFields || value)
              .map(([label, value]) => (
                <Row key={label} label={label}>
                  {value}
                </Row>
              ))}
          </div>
          {rows.some(([, value]) => !value) && (
            <button
              type="button"
              className="link-button"
              aria-expanded={allFields}
              aria-controls={rowsId}
              onClick={() => setAllFields(!allFields)}
            >
              {t(allFields ? "contacts.hideEmptyFields" : "contacts.showAllFields")}
            </button>
          )}
        </>
      ) : (
        <p className="muted small">{t("contacts.passportEmpty")}</p>
      )}
    </section>
  );
}

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

// How many checks the lines fail; Infinity when the API finds no zone in them or cannot be
// reached — the reading shown at the end then reports why.
const failedChecks = (text: string): Promise<number> =>
  apiPost<MrzReading>("/mrz/read", { text }).then(
    (mrz) => mrz.failed.length,
    () => Infinity,
  );

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
      // Each way of reading the photo is checked: the first whose check digits agree is
      // taken, else the one failing the fewest checks, for the specialist to correct.
      let best: string | null = null;
      let fewest = Infinity;
      for await (const text of readZones(file, setProgress)) {
        const failed = await failedChecks(text);
        if (best === null || failed < fewest) {
          best = text;
          fewest = failed;
        }
        if (failed === 0) break;
      }
      if (best === null) {
        setPhotoError("notFound");
        return;
      }
      setLines(best);
      read.mutate(best);
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
      {read.data && (
        <Reading key={read.data.lines.join()} card={card} mrz={read.data} onDone={onDone} />
      )}
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
      key: "last_name_latin",
      label: t("contacts.lastNameLatin"),
      value: mrz.surname || null,
      shown: mrz.surname,
      current: c.last_name_latin,
    },
    {
      key: "first_name_latin",
      label: t("contacts.firstNameLatin"),
      value: mrz.given_names || null,
      shown: mrz.given_names,
      current: c.first_name_latin,
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
    mutationFn: () => {
      const picked = fields.filter((f) => chosen.has(f.key) && f.value !== null);
      const isPassport = (key: string) => (PASSPORT_FIELDS as readonly string[]).includes(key);
      // One change of the card: the passport goes with the questionnaire, the rest on the
      // contact; only the fields sent change.
      return apiPatch(`/contacts/${c.id}`, {
        ...Object.fromEntries(picked.filter((f) => !isPassport(f.key)).map((f) => [f.key, f.value])),
        passport: Object.fromEntries(
          picked.filter((f) => isPassport(f.key)).map((f) => [f.key, f.value]),
        ),
      });
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

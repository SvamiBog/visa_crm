import { useState } from "react";
import { useTranslation } from "react-i18next";
import { formatDate } from "../../contacts";
import {
  promptLanguage,
  useImportCard,
  useImportCards,
  type CardField,
  type CardRecord,
  type CardSection,
  type CardStatus,
  type ImportCard,
  type ImportCardLine,
  type ImportIssue,
} from "../../imports";
import { ErrorAlert } from "../clients/ui";

// Карточки до загрузки (backlog 1.22, docs/data-format.md): each client of the file as the
// CRM will have them once it is loaded. The sections are the format's sheets and the fields
// its columns, named by the server in the interface's language; what the file adds or
// changes is marked. A password is never shown, only that there is one.

// The list shows this many cards at first, then more on demand: a file has thousands.
const LIST_PAGE = 50;
const FILTERS = ["all", "new", "updated", "same", "issues"] as const;
type Filter = (typeof FILTERS)[number];
const STATUS_CHIP: Record<CardStatus, string> = {
  new: "st-green",
  updated: "st-orange",
  same: "st-gray",
};

/** «Лист «Кейсы», строка 7, колонка «Дата подачи»» — never the cell's value. */
export function useIssueText() {
  const { t } = useTranslation();
  return (issue: ImportIssue) => {
    const where = [
      issue.sheet ? t("import.whereSheet", { sheet: issue.sheet }) : null,
      issue.row !== null ? t("import.whereRow", { row: issue.row }) : null,
      issue.column ? t("import.whereColumn", { column: issue.column }) : null,
    ].filter(Boolean);
    const reason = t(`import.reason.${issue.reason}`, { defaultValue: t("import.reason.other") });
    return where.length ? `${where.join(", ")}: ${reason}` : reason;
  };
}

export function CardView({ card }: { card: ImportCard }) {
  const { t } = useTranslation();
  const text = useIssueText();
  return (
    <article className="import-card">
      <header className="import-card-head">
        <strong>{card.name ?? t("import.card.own")}</strong>
        <StatusChip status={card.status} />
        {card.phone && <span className="muted small">{card.phone}</span>}
      </header>
      {card.sections.map((section) => (
        <Section key={section.sheet} section={section} quiet={card.status === "new"} />
      ))}
      {card.issues.length > 0 && (
        <div className="import-card-issues">
          <h4 className="h3">{t("import.card.issues")}</h4>
          <ul className="plain-list import-issue-list">
            {card.issues.map((issue, i) => (
              <li key={`${issue.sheet}-${issue.row}-${issue.column ?? ""}-${i}`}>{text(issue)}</li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}

function StatusChip({ status }: { status: CardStatus }) {
  const { t } = useTranslation();
  return <span className={`chip ${STATUS_CHIP[status]}`}>{t(`import.card.status.${status}`)}</span>;
}

/** A sheet's records. The card's own fields go without the sheet's name: the card's head
 * names the client. In a new card every record is new, so it is not said on each. */
function Section({ section, quiet }: { section: CardSection; quiet: boolean }) {
  return (
    <section className="import-card-section">
      {section.sheet !== "clients" && <h4 className="import-card-title">{section.title}</h4>}
      {section.records.map((record, i) => (
        <RecordView key={i} record={record} quiet={quiet} />
      ))}
    </section>
  );
}

function RecordView({ record, quiet }: { record: CardRecord; quiet: boolean }) {
  const { t } = useTranslation();
  return (
    <div className={`import-record${record.change ? ` import-record-${record.change}` : ""}`}>
      {record.change && !(quiet && record.change === "new") && (
        <span className={`chip ${record.change === "new" ? "st-green" : "st-orange"}`}>
          {t(`import.card.change.${record.change}`)}
        </span>
      )}
      <dl className="import-fields">
        {record.fields.map((field) => (
          <div key={field.key} className="import-field">
            <dt className="muted small">{field.label}</dt>
            <dd>
              <FieldValue field={field} />
            </dd>
          </div>
        ))}
      </dl>
      {record.sections.map((section) => (
        <Section key={section.sheet} section={section} quiet={quiet} />
      ))}
    </div>
  );
}

function FieldValue({ field }: { field: CardField }) {
  const { t, i18n } = useTranslation();
  const { type, value } = field;
  if (type === "secret") return <>{t("import.card.password")}</>;
  if (typeof value === "boolean") return <>{t(value ? "import.card.yes" : "import.card.no")}</>;
  if (type === "date") return <>{formatDate(value, i18n.language)}</>;
  if (type === "money" || type === "number") {
    const digits = type === "money" ? 2 : 0;
    return (
      <span className="nowrap">
        {new Intl.NumberFormat(i18n.language, {
          minimumFractionDigits: digits,
          maximumFractionDigits: type === "money" ? 2 : 6,
        }).format(Number(value))}
      </span>
    );
  }
  return <>{value}</>;
}

/** The cards of the check: a line each, found by name or phone, opened one at a time. */
export function CardList({ uploadId }: { uploadId: string }) {
  const { t } = useTranslation();
  const cards = useImportCards(uploadId);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [shown, setShown] = useState(LIST_PAGE);
  const [open, setOpen] = useState<number | null>(null);
  if (cards.isError) return <ErrorAlert error={cards.error} />;
  if (!cards.data) return <p className="muted">{t("common.loading")}</p>;

  const counts = {
    all: cards.data.length,
    new: cards.data.filter((c) => c.status === "new").length,
    updated: cards.data.filter((c) => c.status === "updated").length,
    same: cards.data.filter((c) => c.status === "same").length,
    issues: cards.data.filter((c) => c.issues > 0).length,
  };
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const digits = query.replace(/\D/g, "");
  const found = cards.data.filter(
    (card) =>
      (filter === "all" ||
        (filter === "issues" ? card.issues > 0 : card.status === filter)) &&
      (words.length === 0 ||
        words.every((w) => (card.name ?? "").toLocaleLowerCase().includes(w)) ||
        (digits.length > 2 && (card.phone ?? "").includes(digits))),
  );

  return (
    <div className="import-cards">
      <div className="form-grid import-card-filters">
        <label className="field">
          <span>{t("import.card.search")}</span>
          <input
            type="search"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setShown(LIST_PAGE);
            }}
          />
        </label>
        <label className="field">
          <span>{t("import.card.filter")}</span>
          <select
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value as Filter);
              setShown(LIST_PAGE);
            }}
          >
            {FILTERS.map((f) => (
              <option key={f} value={f}>
                {t(`import.card.filter.${f}`, { n: counts[f] })}
              </option>
            ))}
          </select>
        </label>
      </div>
      {found.length === 0 && <p className="muted">{t("import.card.nothingFound")}</p>}
      <ul className="plain-list import-card-list">
        {found.slice(0, shown).map((line) => (
          <CardLine
            key={line.n}
            uploadId={uploadId}
            line={line}
            open={open === line.n}
            onToggle={() => setOpen(open === line.n ? null : line.n)}
          />
        ))}
      </ul>
      {found.length > shown && (
        <button type="button" className="btn" onClick={() => setShown(shown + LIST_PAGE)}>
          {t("import.card.more", { n: found.length - shown })}
        </button>
      )}
    </div>
  );
}

function CardLine({
  uploadId,
  line,
  open,
  onToggle,
}: {
  uploadId: string;
  line: ImportCardLine;
  open: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const name = line.name ?? t("import.card.own");
  return (
    <li className="import-card-line">
      <div className="import-card-line-head">
        <button
          type="button"
          className="link-button"
          aria-expanded={open}
          aria-label={t(open ? "import.card.close" : "import.card.open", { name })}
          onClick={onToggle}
        >
          {name}
        </button>
        <StatusChip status={line.status} />
        <span className="muted small">
          {[
            line.phone,
            line.cases ? t("import.card.cases", { count: line.cases }) : null,
            line.consultations
              ? t("import.card.consultations", { count: line.consultations })
              : null,
            line.issues ? t("import.card.issueCount", { count: line.issues }) : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </div>
      {open && <OpenCard uploadId={uploadId} n={line.n} />}
    </li>
  );
}

function OpenCard({ uploadId, n }: { uploadId: string; n: number }) {
  const { t, i18n } = useTranslation();
  const card = useImportCard(uploadId, n, promptLanguage(i18n.resolvedLanguage));
  if (card.isError) return <ErrorAlert error={card.error} />;
  if (!card.data) return <p className="muted">{t("common.loading")}</p>;
  return <CardView card={card.data} />;
}

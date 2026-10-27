import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useDisclosure } from "../../components/Disclosure";
import { formatDate, type VisaCase } from "../../contacts";
import {
  PACKAGE_STATES,
  useMarkItem,
  usePackage,
  type ItemStatus,
  type PackageItem,
  type PackageState,
} from "../../package";
import { ErrorAlert } from "./ui";

// The package check of a case (backlog 2.3): one line on whether the documents are
// collected; the checklist with «есть / нет / не нужно» and the document's date opens
// under it. Statuses are counted to the submission date (backend/app/core/package.py).

const STATUS_CHIP: Record<ItemStatus, string> = {
  ok: "chip st-green",
  missing: "chip st-red",
  no_date: "chip st-orange",
  expired: "chip st-red",
  not_needed: "chip st-gray",
};

/** «Собран» or «не хватает 3»: the package of a case, or of a family's cases. */
export function PackageChip({ ready, missing }: { ready: boolean; missing: number }) {
  const { t } = useTranslation();
  return ready ? (
    <span className="chip st-green">{t("package.ready")}</span>
  ) : (
    <span className="chip st-red">{t("package.missing", { count: missing })}</span>
  );
}

export function CasePackage({ visaCase: c }: { visaCase: VisaCase }) {
  const { t, i18n } = useTranslation();
  const result = usePackage(c);
  const list = useDisclosure();

  if (result.isPending) return null;
  if (result.isError) return <ErrorAlert error={result.error} />;
  const p = result.data;
  const on = formatDate(p.on, i18n.language);

  return (
    <section className="package">
      <div className="card-head">
        <h4 className="h3">{t("package.title")}</h4>
        {p.items.length > 0 && <PackageChip ready={p.ready} missing={p.missing} />}
      </div>
      {p.items.length === 0 ? (
        <p className="muted small">
          {t("package.noChecklist")} <Link to="/app/checklists">{t("nav.checklists")}</Link>
        </p>
      ) : (
        <>
          <p className="muted small">
            {p.on_submission ? t("package.onSubmission", { date: on }) : t("package.onToday")}
          </p>
          <button className="link-button" {...list.button}>
            {list.open ? t("package.hide") : t("package.show", { count: p.items.length })}
          </button>
          <ul className="package-list" {...list.panel}>
            {p.items.map((item) => (
              <Item key={item.item_id} caseId={c.id} item={item} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function Item({ caseId, item }: { caseId: string; item: PackageItem }) {
  const { t } = useTranslation();
  const mark = useMarkItem(caseId, item.item_id);
  // The date is saved when the field is left: typing a year must not save each digit.
  const [date, setDate] = useState(item.document_date ?? "");

  // A document that is not there has no date: the API clears it, and so does the field.
  const setState = (state: PackageState) => {
    if (state !== "present") setDate("");
    mark.mutate({ state, document_date: state === "present" ? date || null : null });
  };
  const saveDate = () => {
    if (date !== (item.document_date ?? ""))
      mark.mutate({ state: "present", document_date: date || null });
  };

  return (
    <li className="package-item">
      <div className="package-line">
        <span>
          <strong>{item.name}</strong>
          {!item.required && <span className="muted small"> · {t("package.optional")}</span>}
        </span>
        <span className={STATUS_CHIP[item.status]}>{t(`package.status.${item.status}`)}</span>
      </div>
      {(item.note || item.max_age_days !== null) && (
        <p className="muted small">
          {[item.max_age_days !== null && t("package.maxAge", { count: item.max_age_days }), item.note]
            .filter(Boolean)
            .join(" · ")}
        </p>
      )}
      <div className="package-mark">
        <label className="field">
          <span>{t("package.state")}</span>
          <select
            value={item.state ?? ""}
            disabled={mark.isPending}
            onChange={(e) => setState(e.target.value as PackageState)}
          >
            {item.state === null && (
              <option value="" disabled>
                {t("package.notMarked")}
              </option>
            )}
            {PACKAGE_STATES.map((s) => (
              <option key={s} value={s}>
                {t(`package.stateValue.${s}`)}
              </option>
            ))}
          </select>
        </label>
        {item.state === "present" && (
          <label className="field">
            <span>{t("package.documentDate")}</span>
            <input
              type="date"
              value={date}
              disabled={mark.isPending}
              onChange={(e) => setDate(e.target.value)}
              onBlur={saveDate}
            />
          </label>
        )}
      </div>
      {mark.isError && <ErrorAlert error={mark.error} />}
    </li>
  );
}

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { apiGet } from "../../api";
import { useMe } from "../../auth";
import { CHECKLISTS_KEY, type Checklist } from "../../checklists";
import { useCountries, useLocalName } from "../../reference";
import { ErrorAlert } from "../clients/ui";
import {
  ChecklistCountrySelect,
  ChecklistItems,
  ItemForm,
  useChecklistCountry,
} from "./ChecklistParts";

type Purpose = { id: string; name: string; is_active: boolean };

/** Workspace: the documents for a visa to a country — the shared list kept by the admin
 * and the specialist's own items; narrowed to what a case like this one needs. */
export function ChecklistsPage() {
  const { t } = useTranslation();
  const me = useMe();
  const canEdit = me.data?.role === "specialist";
  const name = useLocalName();
  const countries = useCountries();
  const purposes = useQuery({
    queryKey: ["dictionaries", "purposes"],
    queryFn: () => apiGet<Purpose[]>("/dictionaries/purposes"),
  });
  const [countryId, setCountryId] = useChecklistCountry(false);
  const [submission, setSubmission] = useState("");
  const [purpose, setPurpose] = useState("");
  const [adding, setAdding] = useState(false);

  const filtered = !!submission || !!purpose;
  const checklist = useQuery({
    queryKey: [...CHECKLISTS_KEY, "workspace", countryId, submission, purpose],
    queryFn: () => {
      const params = new URLSearchParams();
      if (submission) params.set("submission", submission);
      if (purpose) params.set("purpose", purpose);
      return apiGet<Checklist>(`/checklists/${countryId}${filtered ? `?${params}` : ""}`);
    },
    enabled: !!countryId,
    placeholderData: keepPreviousData,
  });
  const submissions = useMemo(
    () =>
      (countries.data ?? [])
        .filter((c) => c.id !== countryId)
        .sort((a, b) => name.country(a).localeCompare(name.country(b))),
    [countries.data, countryId, name],
  );
  // A narrowed list is what a case gets: switched-off items are not in it.
  const own = (checklist.data?.own ?? []).filter((i) => i.is_active || !filtered);

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("checklists.title")}</h1>
          <div className="muted">{t("checklists.subtitle")}</div>
        </div>
      </header>

      <div className="toolbar">
        <label className="field field-inline">
          <span>{t("checklists.country")}</span>
          <ChecklistCountrySelect
            admin={false}
            value={countryId}
            onChange={(id) => {
              setCountryId(id);
              setAdding(false);
            }}
          />
        </label>
        {countryId && (
          <>
            <label className="field field-inline">
              <span>{t("checklists.submission")}</span>
              <select value={submission} onChange={(e) => setSubmission(e.target.value)}>
                <option value="">{t("checklists.anySubmissionFilter")}</option>
                {submissions.map((c) => (
                  <option key={c.id} value={c.id}>
                    {name.country(c)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field field-inline">
              <span>{t("checklists.purpose")}</span>
              <select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
                <option value="">{t("checklists.anyPurposeFilter")}</option>
                {(purposes.data ?? [])
                  .filter((p) => p.is_active)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
              </select>
            </label>
          </>
        )}
      </div>
      {filtered && <p className="muted small">{t("checklists.filteredHint")}</p>}

      {!countryId && <p className="muted">{t("checklists.pickCountry")}</p>}
      {countryId && checklist.isPending && <p className="muted">{t("common.loading")}</p>}
      {checklist.isError && <ErrorAlert error={checklist.error} />}
      {countryId && checklist.data && (
        <>
          <section className="card">
            <div className="card-head">
              <h2 className="h2">{t("checklists.shared")}</h2>
            </div>
            <ChecklistItems
              admin={false}
              countryId={countryId}
              items={checklist.data.shared}
              editable={false}
              reorderable={false}
              empty={t(filtered ? "checklists.noneForCase" : "checklists.sharedEmpty")}
            />
          </section>
          <section className="card">
            <div className="card-head">
              <h2 className="h2">{t("checklists.own")}</h2>
              {canEdit && !adding && (
                <button type="button" className="btn btn-sm" onClick={() => setAdding(true)}>
                  {t("checklists.add")}
                </button>
              )}
            </div>
            {adding && (
              <ItemForm admin={false} countryId={countryId} onDone={() => setAdding(false)} />
            )}
            <ChecklistItems
              admin={false}
              countryId={countryId}
              items={own}
              editable={canEdit}
              reorderable={!filtered}
              empty={t(filtered ? "checklists.noneForCase" : "checklists.ownEmpty")}
            />
          </section>
        </>
      )}
      <p className="muted small">{t("checklists.hint")}</p>
    </div>
  );
}

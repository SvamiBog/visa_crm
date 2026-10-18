import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiDelete, apiDownload, apiGet, apiPost } from "../../api";
import type { VisaCase } from "../../contacts";
import { givenKey, useTemplates, type GivenTemplate } from "../../templates";
import { useCaseOptions } from "../vault/Vault";
import { ErrorAlert } from "./ui";

/** «Документы по шаблонам» on a client's card (backlog 2.1): the specialist's templates
 * given to the client, each downloaded filled with the client's data of that moment and
 * named for the client. */
export function ContactTemplates({ contactId, cases }: { contactId: string; cases: VisaCase[] }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const given = useQuery({
    queryKey: givenKey(contactId),
    queryFn: () => apiGet<GivenTemplate[]>(`/contacts/${contactId}/templates`),
  });
  const caseOptions = useCaseOptions(cases);
  const caseLabel = useMemo(
    () => new Map(caseOptions.map((c) => [c.id, c.label])),
    [caseOptions],
  );
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const url = `/contacts/${contactId}/templates`;
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: givenKey(contactId) });
    // The «how many clients» count on the templates page.
    void queryClient.invalidateQueries({ queryKey: ["templates"], exact: true });
  };

  const download = useMutation({
    mutationFn: (item: GivenTemplate) => apiDownload(`${url}/${item.id}/file`, item.file_name),
    onError: setError,
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiDelete<void>(`${url}/${id}`),
    onSuccess: refresh,
    onError: setError,
  });

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="h2">{t("templates.cardTitle")}</h2>
        {!adding && (
          <button type="button" className="btn btn-sm" onClick={() => setAdding(true)}>
            {t("templates.give")}
          </button>
        )}
      </div>
      {adding && (
        <GiveForm
          contactId={contactId}
          cases={cases}
          given={given.data ?? []}
          onDone={() => {
            setAdding(false);
            refresh();
          }}
          onCancel={() => setAdding(false)}
        />
      )}
      {given.isError && <ErrorAlert error={given.error} />}
      {given.data?.length === 0 && !adding && (
        <p className="muted small">{t("templates.cardEmpty")}</p>
      )}
      {given.data && given.data.length > 0 && (
        <ul className="booking-list templates">
          {given.data.map((item) => (
            <li key={item.id} className="booking-item">
              <div className="booking-line">
                <strong>{item.name}</strong>
                <span className="muted small">
                  {item.case_id ? caseLabel.get(item.case_id) : t("templates.forClient")}
                </span>
              </div>
              <div className="chips">
                {item.missing.length > 0 && (
                  <span className="chip st-orange">
                    {t("templates.missing", {
                      fields: item.missing.map((key) => t(`templates.code.${key}`)).join(", "),
                    })}
                  </span>
                )}
                {item.unknown_codes.length > 0 && (
                  <span className="chip st-orange">
                    {t("templates.unknownCount", { count: item.unknown_codes.length })}
                  </span>
                )}
                {!item.template_active && (
                  <span className="chip st-gray">{t("templates.switchedOff")}</span>
                )}
              </div>
              <div className="note-actions">
                <button
                  type="button"
                  className="link-button"
                  disabled={download.isPending}
                  title={t("templates.downloadHint")}
                  onClick={() => {
                    setError(null);
                    download.mutate(item);
                  }}
                >
                  {t("templates.download", { name: item.file_name })}
                </button>
                <button
                  type="button"
                  className="link-button"
                  disabled={remove.isPending}
                  onClick={() => {
                    setError(null);
                    if (window.confirm(t("templates.confirmTakeBack", { name: item.name })))
                      remove.mutate(item.id);
                  }}
                >
                  {t("templates.takeBack")}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {error !== null && <ErrorAlert error={error} />}
      <p className="muted small">
        {t("templates.cardHint")} <Link to="/app/templates">{t("templates.cardLink")}</Link>
      </p>
    </section>
  );
}

function GiveForm({
  contactId,
  cases,
  given,
  onDone,
  onCancel,
}: {
  contactId: string;
  cases: VisaCase[];
  given: GivenTemplate[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const templates = useTemplates();
  const caseOptions = useCaseOptions(cases);
  // The newest case by default: a letter is mostly for the trip being prepared.
  const newest = [...cases].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const [caseId, setCaseId] = useState(newest?.id ?? "");
  const taken = new Set(
    given.filter((g) => (g.case_id ?? "") === caseId).map((g) => g.template_id),
  );
  const offered = (templates.data ?? []).filter((tpl) => tpl.is_active && !taken.has(tpl.id));
  const [templateId, setTemplateId] = useState("");
  const chosen = offered.some((tpl) => tpl.id === templateId) ? templateId : offered[0]?.id ?? "";

  const save = useMutation({
    mutationFn: () =>
      apiPost<{ id: string }>(`/contacts/${contactId}/templates`, {
        template_id: chosen,
        case_id: caseId || null,
      }),
    onSuccess: onDone,
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (chosen) save.mutate();
  };

  if (templates.data && templates.data.filter((tpl) => tpl.is_active).length === 0) {
    return (
      <p className="muted small">
        {t("templates.noTemplates")} <Link to="/app/templates">{t("templates.cardLink")}</Link>
      </p>
    );
  }
  return (
    <form className="form-card booking-form" onSubmit={submit}>
      <div className="form-grid">
        <label className="field">
          <span>{t("templates.template")}</span>
          <select value={chosen} onChange={(e) => setTemplateId(e.target.value)}>
            {offered.length === 0 && <option value="">{t("templates.allGiven")}</option>}
            {offered.map((tpl) => (
              <option key={tpl.id} value={tpl.id}>
                {tpl.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("templates.case")}</span>
          <select value={caseId} onChange={(e) => setCaseId(e.target.value)}>
            {caseOptions.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
            <option value="">{t("templates.forClient")}</option>
          </select>
          <small className="muted">{t("templates.caseHint")}</small>
        </label>
      </div>
      {save.isError && <ErrorAlert error={save.error} />}
      <div className="form-actions">
        <button type="submit" className="btn btn-sm btn-p" disabled={!chosen || save.isPending}>
          {t("templates.giveSave")}
        </button>
        <button type="button" className="btn btn-sm" onClick={onCancel}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

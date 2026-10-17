import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { apiPatch, apiPost, apiPut } from "../../api";
import {
  CHECKLISTS_KEY,
  PURPOSE_KINDS,
  checklistBase,
  useChecklistCounts,
  type ChecklistItem,
  type PurposeKind,
} from "../../checklists";
import { byId, useCountries, useLocalName, type Country } from "../../reference";
import { ErrorAlert } from "../clients/ui";

// Pieces of the document checklists (backlog 2.2) shared by the admin's reference and the
// specialist's page: the country to choose, a list of items and an item's form. The admin
// edits the shared list; a specialist edits her own items and reads the shared ones.

function useSortedCountries(admin: boolean): Country[] {
  const countries = useCountries(admin);
  const name = useLocalName();
  return useMemo(
    () =>
      [...(countries.data ?? [])].sort((a, b) => name.country(a).localeCompare(name.country(b))),
    [countries.data, name],
  );
}

/** The country shown: starts on the first one (by name) that has a list. */
export function useChecklistCountry(admin: boolean): [string, (id: string) => void] {
  const [countryId, setCountryId] = useState("");
  const counts = useChecklistCounts(admin);
  const sorted = useSortedCountries(admin);
  useEffect(() => {
    if (countryId || !counts.data || sorted.length === 0) return;
    const listed = new Set(counts.data.map((c) => c.country_id));
    const first = sorted.find((c) => listed.has(c.id));
    if (first) setCountryId(first.id);
  }, [counts.data, sorted, countryId]);
  return [countryId, setCountryId];
}

/** Countries that have a list come first, with the number of items in use. */
export function ChecklistCountrySelect({
  admin,
  value,
  onChange,
}: {
  admin: boolean;
  value: string;
  onChange: (id: string) => void;
}) {
  const { t } = useTranslation();
  const name = useLocalName();
  const sorted = useSortedCountries(admin);
  const counts = useChecklistCounts(admin);
  const count = useMemo(
    () => new Map((counts.data ?? []).map((c) => [c.country_id, c.count])),
    [counts.data],
  );
  const option = (c: Country) => (
    <option key={c.id} value={c.id}>
      {count.has(c.id)
        ? t("checklists.countryCount", { country: name.country(c), count: count.get(c.id) })
        : name.country(c)}
    </option>
  );
  const listed = sorted.filter((c) => count.has(c.id));
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="" disabled>
        {t("checklists.chooseCountry")}
      </option>
      {listed.length > 0 && <optgroup label={t("checklists.withList")}>{listed.map(option)}</optgroup>}
      <optgroup label={t("checklists.withoutList")}>
        {sorted.filter((c) => !count.has(c.id)).map(option)}
      </optgroup>
    </select>
  );
}

/** When the document is needed and what it must be like. */
function ItemFacts({ item, admin }: { item: ChecklistItem; admin: boolean }) {
  const { t } = useTranslation();
  const name = useLocalName();
  const countries = useCountries(admin);
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  return (
    <>
      <div className="chips">
        {item.required ? (
          <span className="chip st-blue">{t("checklists.required")}</span>
        ) : (
          <span className="chip st-gray">{t("checklists.conditional")}</span>
        )}
        {item.max_age_days !== null && (
          <span className="chip st-orange">
            {t("checklists.maxAge", { days: item.max_age_days })}
          </span>
        )}
        {item.purposes.length > 0 && (
          <span className="chip st-gray">
            {t("checklists.forPurposes", {
              purposes: item.purposes.map((k) => t(`purposeKind.${k}`)).join(", "),
            })}
          </span>
        )}
        {item.submission_id && (
          <span className="chip st-gray">
            {t("checklists.onlySubmission", {
              country: name.country(countryById.get(item.submission_id)),
            })}
          </span>
        )}
        {!item.is_active && <span className="chip st-gray">{t("reference.off")}</span>}
      </div>
      {item.note && <p className="note-body small">{item.note}</p>}
    </>
  );
}

/** One list of items. An editable list can be changed, switched off and reordered;
 * a list narrowed by a filter is not reordered, since some of its items are hidden. */
export function ChecklistItems({
  admin,
  countryId,
  items,
  editable,
  reorderable,
  empty,
}: {
  admin: boolean;
  countryId: string;
  items: ChecklistItem[];
  editable: boolean;
  reorderable: boolean;
  empty: string;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const base = checklistBase(admin);
  const [editing, setEditing] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: CHECKLISTS_KEY });
  const toggle = useMutation({
    mutationFn: (item: ChecklistItem) =>
      apiPatch<ChecklistItem>(`${base}/items/${item.id}`, { is_active: !item.is_active }),
    onSuccess: refresh,
  });
  const move = useMutation({
    mutationFn: ({ from, to }: { from: number; to: number }) => {
      const ids = items.map((i) => i.id);
      [ids[from], ids[to]] = [ids[to], ids[from]];
      return apiPut<ChecklistItem[]>(`${base}/${countryId}/order`, { ids });
    },
    onSuccess: refresh,
  });

  if (items.length === 0) return <p className="muted small">{empty}</p>;
  return (
    <>
      {toggle.isError && <ErrorAlert error={toggle.error} />}
      {move.isError && <ErrorAlert error={move.error} />}
      <ul className="booking-list checklist">
        {items.map((item, i) => (
          <li key={item.id} className={item.is_active ? "booking-item" : "booking-item row-off"}>
            {editing === item.id ? (
              <ItemForm
                admin={admin}
                countryId={countryId}
                item={item}
                onDone={() => setEditing(null)}
              />
            ) : (
              <>
                <div className="booking-line">
                  <strong>{item.name}</strong>
                </div>
                <ItemFacts item={item} admin={admin} />
                {editable && (
                  <div className="note-actions">
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => setEditing(item.id)}
                    >
                      {t("reference.edit")}
                    </button>
                    <button
                      type="button"
                      className="link-button"
                      disabled={toggle.isPending}
                      onClick={() => toggle.mutate(item)}
                    >
                      {t(item.is_active ? "reference.switchOff" : "reference.switchOn")}
                    </button>
                    {reorderable && (
                      <>
                        <button
                          type="button"
                          className="link-button"
                          disabled={i === 0 || move.isPending}
                          onClick={() => move.mutate({ from: i, to: i - 1 })}
                        >
                          {t("checklists.up")}
                        </button>
                        <button
                          type="button"
                          className="link-button"
                          disabled={i === items.length - 1 || move.isPending}
                          onClick={() => move.mutate({ from: i, to: i + 1 })}
                        >
                          {t("checklists.down")}
                        </button>
                      </>
                    )}
                  </div>
                )}
              </>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}

/** A new item at the end of the list, or the changes to one. */
export function ItemForm({
  admin,
  countryId,
  item,
  onDone,
}: {
  admin: boolean;
  countryId: string;
  item?: ChecklistItem;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const name = useLocalName();
  const countries = useSortedCountries(admin);
  const [title, setTitle] = useState(item?.name ?? "");
  const [required, setRequired] = useState(item?.required ?? true);
  const [maxAge, setMaxAge] = useState(item?.max_age_days?.toString() ?? "");
  const [purposes, setPurposes] = useState<PurposeKind[]>(item?.purposes ?? []);
  const [submission, setSubmission] = useState(item?.submission_id ?? "");
  const [note, setNote] = useState(item?.note ?? "");
  const base = checklistBase(admin);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: title.trim(),
        required,
        max_age_days: maxAge ? Number(maxAge) : null,
        purposes,
        submission_id: submission || null,
        note: note.trim() || null,
      };
      return item
        ? apiPatch<ChecklistItem>(`${base}/items/${item.id}`, body)
        : apiPost<ChecklistItem>(`${base}/${countryId}/items`, body);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: CHECKLISTS_KEY });
      onDone();
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  const togglePurpose = (kind: PurposeKind, on: boolean) =>
    setPurposes(on ? [...purposes, kind] : purposes.filter((k) => k !== kind));

  return (
    <form className="form-card booking-form" onSubmit={submit}>
      <label className="field">
        <span>{t("checklists.name")}</span>
        <input
          required
          autoFocus
          maxLength={200}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <div className="form-grid">
        <label className="field">
          <span>{t("checklists.need")}</span>
          <select
            value={required ? "required" : "conditional"}
            onChange={(e) => setRequired(e.target.value === "required")}
          >
            <option value="required">{t("checklists.required")}</option>
            <option value="conditional">{t("checklists.conditional")}</option>
          </select>
        </label>
        <label className="field">
          <span>{t("checklists.maxAgeLabel")}</span>
          <input
            type="number"
            inputMode="numeric"
            min={1}
            max={365}
            value={maxAge}
            onChange={(e) => setMaxAge(e.target.value)}
          />
          <small className="muted">{t("checklists.maxAgeHint")}</small>
        </label>
        <label className="field">
          <span>{t("checklists.submission")}</span>
          <select value={submission} onChange={(e) => setSubmission(e.target.value)}>
            <option value="">{t("checklists.anySubmission")}</option>
            {countries
              .filter((c) => c.id !== countryId)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {name.country(c)}
                </option>
              ))}
          </select>
          <small className="muted">{t("checklists.submissionHint")}</small>
        </label>
      </div>
      <fieldset className="module-block checklist-purposes">
        <legend>{t("checklists.purposes")}</legend>
        <div className="checklist-kinds">
          {PURPOSE_KINDS.map((kind) => (
            <label key={kind} className="checkbox">
              <input
                type="checkbox"
                checked={purposes.includes(kind)}
                onChange={(e) => togglePurpose(kind, e.target.checked)}
              />
              <span>{t(`purposeKind.${kind}`)}</span>
            </label>
          ))}
        </div>
        <small className="muted">{t("checklists.purposesHint")}</small>
      </fieldset>
      <label className="field">
        <span>{t("checklists.note")}</span>
        <textarea rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
        <small className="muted">{t("checklists.noteHint")}</small>
      </label>
      {save.isError && <ErrorAlert error={save.error} />}
      <div className="form-actions">
        <button type="submit" className="btn btn-sm btn-p" disabled={save.isPending || !title.trim()}>
          {t("common.save")}
        </button>
        <button type="button" className="btn btn-sm" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

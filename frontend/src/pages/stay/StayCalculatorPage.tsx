import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { calculateStay, type TripDraft } from "../../stay";
import { deferDelete, flushPending } from "../../undo";
import { ErrorAlert } from "../clients/ui";
import { CountrySelect, StaySummaryView, TripCheck, TripResultLine } from "./StayParts";

// «Калькулятор 90/180» (backlog 2.6): days in the Schengen area for trips typed in here,
// say during a consultation, before the person is a client. Nothing is saved: a
// client's trips are kept in their card. The trips are on the left and the answer next to
// them, so it is in view while the dates are typed.

type Row = TripDraft & { key: number };

const CLEAR_KEY = "stay-calculator";

export function StayCalculatorPage() {
  const { t } = useTranslation();
  const nextKey = useRef(1);
  const [rows, setRows] = useState<Row[]>([blank(0)]);
  // Rows without a country or an entry date are not counted yet.
  const ready = rows.filter((r) => r.country_id && r.entered_on);
  const result = useQuery({
    queryKey: ["stay", "calculator", ready.map(({ key: _, ...trip }) => trip)],
    // The answer remembers which rows it was for: while a new one is on its way the old
    // one stays on screen, and must not slip onto another row if rows were added or removed.
    queryFn: async () => ({ keys: ready.map((r) => r.key), stay: await calculateStay(ready) }),
    placeholderData: keepPreviousData,
  });
  const stay = result.data?.stay;
  const results = new Map(result.data?.keys.map((key, i) => [key, stay?.trips[i]] as const));

  const change = (key: number, patch: Partial<TripDraft>) =>
    setRows((current) => current.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  const filled = rows.some((r) => r.country_id || r.entered_on || r.left_on);
  // Cleared at once; «Отменить» puts the rows back. Nothing is on the server, so leaving
  // the page ends the offer.
  const clear = () => {
    const saved = rows;
    setRows([blank(nextKey.current++)]);
    deferDelete({ key: CLEAR_KEY, message: t("undo.tripsCleared"), restore: () => setRows(saved) });
  };
  useEffect(() => () => void flushPending(CLEAR_KEY), []);

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("stay.pageTitle")}</h1>
          <div className="muted">{t("stay.pageSubtitle")}</div>
        </div>
      </header>

      <div className="stay-layout">
        <section className="card stay-block">
          <div className="card-head">
            <h2 className="h2">{t("stay.tripsTitle")}</h2>
            <div className="card-head-actions">
              {filled && (
                <button type="button" className="btn btn-sm" onClick={clear}>
                  {t("stay.clear")}
                </button>
              )}
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setRows((current) => [...current, blank(nextKey.current++)])}
              >
                {t("stay.add")}
              </button>
            </div>
          </div>
          <p className="muted small stay-intro">{t("stay.tripsHint")}</p>
          <div className="stay-rows">
            {rows.map((row, i) => (
              <div key={row.key} className="stay-trip">
                <div className="stay-row">
                  <label className="field">
                    <span>{t("stay.country")}</span>
                    <CountrySelect
                      value={row.country_id}
                      onChange={(id) => change(row.key, { country_id: id })}
                    />
                  </label>
                  <label className="field">
                    <span>{t("stay.entry")}</span>
                    <input
                      type="date"
                      value={row.entered_on}
                      onChange={(e) => change(row.key, { entered_on: e.target.value })}
                    />
                  </label>
                  <label className="field">
                    <span>{t("stay.exit")}</span>
                    <input
                      type="date"
                      min={row.entered_on || undefined}
                      value={row.left_on ?? ""}
                      onChange={(e) => change(row.key, { left_on: e.target.value || null })}
                    />
                  </label>
                  {rows.length > 1 && (
                    <button
                      type="button"
                      className="link-button"
                      aria-label={t("stay.removeTrip", { number: i + 1 })}
                      onClick={() => setRows((current) => current.filter((r) => r.key !== row.key))}
                    >
                      {t("vault.delete")}
                    </button>
                  )}
                </div>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={row.long_stay}
                    onChange={(e) => change(row.key, { long_stay: e.target.checked })}
                  />
                  {t("stay.longStay")}
                </label>
                {row.country_id && row.entered_on ? (
                  stay && (
                    <TripResultLine
                      trip={row}
                      result={results.get(row.key)}
                      maxDays={stay.max_days}
                    />
                  )
                ) : (
                  <p className="muted small stay-pending">{t("stay.rowIncomplete")}</p>
                )}
              </div>
            ))}
          </div>
          <p className="muted small">{t("stay.exitHint")}</p>
        </section>

        <section className="card stay-block stay-result">
          <h2 className="h2">{t("stay.resultTitle")}</h2>
          {result.isError && <ErrorAlert error={result.error} />}
          {stay && <StaySummaryView stay={stay} />}
          <TripCheck trips={ready} />
          <p className="muted small">
            {t("stay.rule")} {t("stay.nothingSaved")}
          </p>
        </section>
      </div>
    </div>
  );
}

function blank(key: number): Row {
  return { key, country_id: "", entered_on: "", left_on: null, long_stay: false };
}

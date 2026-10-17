import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { calculateStay, type TripDraft } from "../../stay";
import { ErrorAlert } from "../clients/ui";
import { CountrySelect, StaySummaryView, TripCheck, TripFacts } from "./StayParts";

// «Калькулятор 90/180» (backlog 2.6): days in the Schengen area for trips typed in here,
// say during a consultation, before the person is a client. Nothing is saved: a
// client's trips are kept in their card.

type Row = TripDraft & { key: number };

export function StayCalculatorPage() {
  const { t } = useTranslation();
  const nextKey = useRef(1);
  const [rows, setRows] = useState<Row[]>([blank(0)]);
  // Rows without a country or an entry date are not counted yet.
  const ready = rows.filter((r) => r.country_id && r.entered_on);
  const result = useQuery({
    queryKey: ["stay", "calculator", ready.map(({ key: _, ...trip }) => trip)],
    queryFn: () => calculateStay(ready),
    placeholderData: keepPreviousData,
  });
  const results = new Map(
    ready.map((r, i) => [r.key, result.data?.trips[i]] as const),
  );

  const change = (key: number, patch: Partial<TripDraft>) =>
    setRows((current) => current.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("stay.pageTitle")}</h1>
          <div className="muted">{t("stay.pageSubtitle")}</div>
        </div>
      </header>

      <section className="card stay-block">
        <div className="card-head">
          <h2 className="h2">{t("stay.tripsTitle")}</h2>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setRows((current) => [...current, blank(nextKey.current++)])}
          >
            {t("stay.add")}
          </button>
        </div>
        <div className="stay-rows">
          {rows.map((row) => (
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
                <button
                  type="button"
                  className="link-button"
                  onClick={() => setRows((current) => current.filter((r) => r.key !== row.key))}
                >
                  {t("vault.delete")}
                </button>
              </div>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={row.long_stay}
                  onChange={(e) => change(row.key, { long_stay: e.target.checked })}
                />
                {t("stay.longStay")}
              </label>
              {results.get(row.key) && result.data && (
                <TripFacts
                  trip={row}
                  result={results.get(row.key)}
                  maxDays={result.data.max_days}
                />
              )}
            </div>
          ))}
        </div>
        <p className="muted small">{t("stay.exitHint")}</p>
      </section>

      <section className="card stay-block">
        <h2 className="h2">{t("stay.resultTitle")}</h2>
        {result.isError && <ErrorAlert error={result.error} />}
        {result.data && <StaySummaryView stay={result.data} />}
        <TripCheck trips={ready} />
        <p className="muted small">
          {t("stay.rule")} {t("stay.nothingSaved")}
        </p>
      </section>
    </div>
  );
}

function blank(key: number): Row {
  return { key, country_id: "", entered_on: "", left_on: null, long_stay: false };
}

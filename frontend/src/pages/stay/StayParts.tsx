import { useMutation } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { formatDate } from "../../contacts";
import { useCountries, useLocalName, type Country } from "../../reference";
import {
  calculateStay,
  daysBetween,
  type Stay,
  type StayCheck,
  type TripDraft,
  type TripResult,
} from "../../stay";
import { ErrorAlert } from "../clients/ui";

// Pieces of the 90/180 calculator (backlog 2.6) shared by the client card and the
// calculator page. The count itself is done by the API (backend/app/core/stay.py).

/** Countries of the Schengen area first: only their days count. */
export function CountrySelect({
  value,
  onChange,
  required,
}: {
  value: string;
  onChange: (id: string) => void;
  required?: boolean;
}) {
  const { t } = useTranslation();
  const countries = useCountries();
  const name = useLocalName();
  const [area, other] = useMemo(() => {
    const sorted = [...(countries.data ?? [])].sort((a, b) =>
      name.country(a).localeCompare(name.country(b)),
    );
    return [sorted.filter((c) => c.schengen_since), sorted.filter((c) => !c.schengen_since)];
  }, [countries.data, name]);
  const option = (c: Country) => (
    <option key={c.id} value={c.id}>
      {name.country(c)}
    </option>
  );
  return (
    <select required={required} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="" />
      <optgroup label={t("stay.schengenArea")}>{area.map(option)}</optgroup>
      <optgroup label={t("stay.otherCountries")}>{other.map(option)}</optgroup>
    </select>
  );
}

function meterClass(used: number, max: number): string {
  if (used >= max) return "stay-meter is-full";
  return used >= max * 0.75 ? "stay-meter is-high" : "stay-meter";
}

/** Days used, how long the person may stay from today, overstays. */
export function StaySummaryView({ stay }: { stay: Stay }) {
  const { t, i18n } = useTranslation();
  const date = (value: string | null) => formatDate(value, i18n.language);
  const s = stay.summary;
  const left = s.stay_until ? daysBetween(stay.today, s.stay_until) : 0;
  return (
    <>
      <div className="kpis">
        <div className="kpi">
          <span className="kpi-label">{t("stay.used", { days: stay.window_days })}</span>
          <span className="kpi-value">
            {s.used} <small>{t("stay.ofMax", { max: stay.max_days })}</small>
          </span>
          <span className={meterClass(s.used, stay.max_days)} aria-hidden="true">
            <span style={{ width: `${Math.min(100, (s.used / stay.max_days) * 100)}%` }} />
          </span>
        </div>
        {s.in_area ? (
          <div className="kpi">
            <span className="kpi-label">{t("stay.leaveBy")}</span>
            <span className="kpi-value">{s.stay_until ? date(s.stay_until) : t("stay.now")}</span>
            <span className="kpi-note">
              {!s.stay_until
                ? t("stay.overstayNow")
                : left > 1
                  ? t("stay.daysMore", { count: left - 1 })
                  : t("stay.lastDayToday")}
            </span>
          </div>
        ) : s.stay_until ? (
          <div className="kpi">
            <span className="kpi-label">{t("stay.enterToday")}</span>
            <span className="kpi-value">{t("stay.until", { date: date(s.stay_until) })}</span>
            <span className="kpi-note">{t("stay.daysCount", { count: left })}</span>
          </div>
        ) : (
          <div className="kpi">
            <span className="kpi-label">{t("stay.noEntryToday")}</span>
            <span className="kpi-value">{date(s.next_entry)}</span>
            <span className="kpi-note">{t("stay.nextEntry")}</span>
          </div>
        )}
        <div className="kpi">
          <span className="kpi-label">{t("stay.fullStay", { max: stay.max_days })}</span>
          <span className="kpi-value">
            {s.full_stay_from === stay.today
              ? t("stay.fromToday")
              : t("stay.fromDate", { date: date(s.full_stay_from) })}
          </span>
        </div>
      </div>
      {s.in_area && <p className="chip st-blue stay-now">{t("stay.inAreaNow")}</p>}
      {s.overstays.map((p) => (
        <p key={p.first} className="alert alert-error small">
          {t("stay.overstay", {
            first: date(p.first),
            last: date(p.last),
            count: daysBetween(p.first, p.last),
          })}
        </p>
      ))}
    </>
  );
}

/** «12 дн. · на выезде 47 из 90» and the chips of one trip. */
export function TripFacts({
  trip,
  result,
  maxDays,
}: {
  trip: TripDraft;
  result?: TripResult;
  maxDays: number;
}) {
  const { t, i18n } = useTranslation();
  const date = (value: string | null) => formatDate(value, i18n.language);
  return (
    <div className="muted small stay-facts">
      <span>
        {date(trip.entered_on)} – {trip.left_on ? date(trip.left_on) : t("stay.stillThere")}
      </span>
      {result?.counts && <span>{t("stay.daysCount", { count: result.days })}</span>}
      {result?.counts && result.used_at_end !== null && (
        <span>
          {t(trip.left_on ? "stay.atExit" : "stay.byToday", {
            used: result.used_at_end,
            max: maxDays,
          })}
        </span>
      )}
      {result && !result.counts && (
        <span className="chip st-gray">
          {t(trip.long_stay ? "stay.longStayChip" : "stay.outsideChip")}
        </span>
      )}
      {result?.overstay_from && (
        <span className="chip st-red">
          {t("stay.overstayFrom", { date: date(result.overstay_from) })}
        </span>
      )}
    </div>
  );
}

/** «Проверить поездку»: a planned trip against the trips given, nothing saved. */
export function TripCheck({ trips }: { trips: TripDraft[] }) {
  const { t } = useTranslation();
  const [enteredOn, setEnteredOn] = useState("");
  const [leftOn, setLeftOn] = useState("");
  const run = useMutation({
    mutationFn: () =>
      calculateStay(trips, { entered_on: enteredOn, left_on: leftOn || null }),
  });
  // An answer for other trips would mislead: it goes once the trips change.
  const signature = JSON.stringify(trips);
  const { reset } = run;
  useEffect(() => reset(), [signature, reset]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    run.mutate();
  };
  return (
    <form className="stay-check" onSubmit={submit}>
      <h3 className="h3">{t("stay.checkTitle")}</h3>
      <div className="form-grid">
        <label className="field">
          <span>{t("stay.entry")}</span>
          <input
            type="date"
            required
            value={enteredOn}
            onChange={(e) => setEnteredOn(e.target.value)}
          />
        </label>
        <label className="field">
          <span>{t("stay.exitOptional")}</span>
          <input
            type="date"
            min={enteredOn || undefined}
            value={leftOn}
            onChange={(e) => setLeftOn(e.target.value)}
          />
        </label>
      </div>
      <div className="form-actions">
        <button type="submit" className="btn btn-sm btn-p" disabled={run.isPending}>
          {t("stay.check")}
        </button>
      </div>
      {run.isError && <ErrorAlert error={run.error} />}
      {run.data?.check && (
        <CheckResult
          check={run.data.check}
          enteredOn={enteredOn}
          maxDays={run.data.max_days}
        />
      )}
    </form>
  );
}

function CheckResult({
  check: c,
  enteredOn,
  maxDays,
}: {
  check: StayCheck;
  enteredOn: string;
  maxDays: number;
}) {
  const { t, i18n } = useTranslation();
  const date = (value: string | null) => formatDate(value, i18n.language);
  const used = t("stay.usedBefore", { used: c.used_before, max: maxDays });
  if (c.stay_until === null) {
    return (
      <p className="alert alert-error small">
        {t("stay.cannotEnter", { date: date(enteredOn) })} {used}{" "}
        {c.earliest_entry && t("stay.earliestEntry", { date: date(c.earliest_entry) })}
      </p>
    );
  }
  const until = t("stay.mayStay", {
    date: date(c.stay_until),
    count: daysBetween(enteredOn, c.stay_until),
  });
  if (c.fits === null) {
    return (
      <p className="alert alert-ok small">
        {used} {until}
      </p>
    );
  }
  if (c.fits) {
    return (
      <p className="alert alert-ok small">
        {t("stay.fits", { count: c.length ?? 0 })} {used} {until}
      </p>
    );
  }
  return (
    <p className="alert alert-error small">
      {t("stay.doesNotFit", { count: c.length ?? 0 })} {used} {until}{" "}
      {c.earliest_entry
        ? t("stay.fitsFrom", { count: c.length ?? 0, date: date(c.earliest_entry) })
        : t("stay.tooLong", { max: maxDays })}
    </p>
  );
}

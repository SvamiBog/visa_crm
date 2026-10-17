import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiDelete, apiGet, apiPatch, apiPost } from "../../api";
import { byId, useCountries, useLocalName } from "../../reference";
import { tripsKey, type ContactTrips as ContactTripsData, type Trip, type TripResult } from "../../stay";
import { CountrySelect, StaySummaryView, TripCheck, TripFacts } from "../stay/StayParts";
import { ErrorAlert } from "./ui";

// «Поездки и 90/180» in the client's card (backlog 2.6): the person's trips, the days
// spent in the Schengen area and a check of a planned trip.

export function ContactTrips({ contactId }: { contactId: string }) {
  const { t } = useTranslation();
  const data = useQuery({
    queryKey: tripsKey(contactId),
    queryFn: () => apiGet<ContactTripsData>(`/contacts/${contactId}/trips`),
  });
  const [editing, setEditing] = useState<Trip | "new" | null>(null);

  return (
    <section className="card stay-block">
      <div className="card-head">
        <h2 className="h2">{t("stay.cardTitle")}</h2>
        {editing === null && (
          <button type="button" className="btn btn-sm" onClick={() => setEditing("new")}>
            {t("stay.add")}
          </button>
        )}
      </div>
      {data.isPending && <p className="muted">{t("common.loading")}</p>}
      {data.isError && <ErrorAlert error={data.error} />}
      {editing !== null && (
        <TripForm
          key={editing === "new" ? "new" : editing.id}
          contactId={contactId}
          trip={editing === "new" ? undefined : editing}
          onDone={() => setEditing(null)}
        />
      )}
      {data.data && (
        <>
          {data.data.trips.length === 0 && editing === null ? (
            <p className="muted small">{t("stay.empty")}</p>
          ) : (
            <StaySummaryView stay={data.data.stay} />
          )}
          <ul className="booking-list">
            {data.data.trips.map((trip, i) => (
              <TripItem
                key={trip.id}
                contactId={contactId}
                trip={trip}
                result={data.data.stay.trips[i]}
                maxDays={data.data.stay.max_days}
                onEdit={() => setEditing(trip)}
              />
            ))}
          </ul>
          <TripCheck trips={data.data.trips} />
          <p className="muted small">
            {t("stay.rule")} <Link to="/app/stay-calculator">{t("stay.openCalculator")}</Link>
          </p>
        </>
      )}
    </section>
  );
}

function TripItem({
  contactId,
  trip,
  result,
  maxDays,
  onEdit,
}: {
  contactId: string;
  trip: Trip;
  result?: TripResult;
  maxDays: number;
  onEdit: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const name = useLocalName();
  const remove = useMutation({
    mutationFn: () => apiDelete<void>(`/trips/${trip.id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: tripsKey(contactId) }),
  });
  return (
    <li className={result?.counts === false ? "booking-item row-off" : "booking-item"}>
      <div className="booking-line">
        <strong>{name.country(countryById.get(trip.country_id)) || "—"}</strong>
      </div>
      <TripFacts trip={trip} result={result} maxDays={maxDays} />
      {trip.note && <p className="note-body small">{trip.note}</p>}
      {remove.isError && <ErrorAlert error={remove.error} />}
      <div className="note-actions">
        <button type="button" className="link-button" onClick={onEdit}>
          {t("reference.edit")}
        </button>
        <button
          type="button"
          className="link-button"
          disabled={remove.isPending}
          onClick={() => {
            if (window.confirm(t("stay.confirmDelete"))) remove.mutate();
          }}
        >
          {t("vault.delete")}
        </button>
      </div>
    </li>
  );
}

function TripForm({
  contactId,
  trip,
  onDone,
}: {
  contactId: string;
  trip?: Trip;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [country, setCountry] = useState(trip?.country_id ?? "");
  const [enteredOn, setEnteredOn] = useState(trip?.entered_on ?? "");
  const [leftOn, setLeftOn] = useState(trip?.left_on ?? "");
  const [longStay, setLongStay] = useState(trip?.long_stay ?? false);
  const [note, setNote] = useState(trip?.note ?? "");

  const save = useMutation({
    mutationFn: () => {
      const body = {
        country_id: country,
        entered_on: enteredOn,
        left_on: leftOn || null,
        long_stay: longStay,
        note: note.trim() || null,
      };
      return trip
        ? apiPatch<Trip>(`/trips/${trip.id}`, body)
        : apiPost<Trip>(`/contacts/${contactId}/trips`, body);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: tripsKey(contactId) });
      onDone();
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form className="form-card booking-form" onSubmit={submit}>
      <div className="form-grid">
        <label className="field">
          <span>{t("stay.country")}</span>
          <CountrySelect required value={country} onChange={setCountry} />
        </label>
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
          <span>{t("stay.exit")}</span>
          <input
            type="date"
            min={enteredOn || undefined}
            value={leftOn}
            onChange={(e) => setLeftOn(e.target.value)}
          />
          <small className="muted">{t("stay.exitHint")}</small>
        </label>
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={longStay} onChange={(e) => setLongStay(e.target.checked)} />
        {t("stay.longStay")}
      </label>
      <label className="field">
        <span>{t("vault.note")}</span>
        <input maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      {save.isError && <ErrorAlert error={save.error} />}
      <div className="form-actions">
        <button type="submit" className="btn btn-sm btn-p" disabled={save.isPending}>
          {t("common.save")}
        </button>
        <button type="button" className="btn btn-sm" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

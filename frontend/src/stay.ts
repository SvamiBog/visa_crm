import { apiPost } from "./api";

// The 90/180 calculator (backend/app/api/stay.py): a client's trips and the count of
// days in the Schengen area. Trips go in a POST body: dates of a person's travels are
// personal data and stay out of URLs.

export type TripDraft = {
  country_id: string;
  entered_on: string;
  // null: still there.
  left_on: string | null;
  // Under a long-stay visa (D) or a residence permit: the days do not count.
  long_stay: boolean;
};

export type Trip = TripDraft & { id: string; note: string | null };

export type StayPeriod = { first: string; last: string };

export type StaySummary = {
  used: number;
  in_area: boolean;
  // In the area: the day to leave by; outside: the last day of a stay entering today.
  stay_until: string | null;
  // The first day from today any stay fits: today, unless the period is full.
  next_entry: string;
  full_stay_from: string;
  overstays: StayPeriod[];
};

export type TripResult = {
  counts: boolean;
  days: number;
  used_at_end: number | null;
  overstay_from: string | null;
};

export type StayCheck = {
  length: number | null;
  used_before: number;
  stay_until: string | null;
  fits: boolean | null;
  earliest_entry: string | null;
};

export type Stay = {
  today: string;
  max_days: number;
  window_days: number;
  summary: StaySummary;
  // In the order the trips were given.
  trips: TripResult[];
  check: StayCheck | null;
};

export type ContactTrips = { trips: Trip[]; stay: Stay };

export function tripsKey(contactId: string) {
  return ["contacts", contactId, "trips"] as const;
}

export function calculateStay(
  trips: TripDraft[],
  check?: { entered_on: string; left_on: string | null },
) {
  return apiPost<Stay>("/stay/calculate", {
    trips: trips.map(({ country_id, entered_on, left_on, long_stay }) => ({
      country_id,
      entered_on,
      left_on,
      long_stay,
    })),
    check: check ?? null,
  });
}

/** Days between two ISO dates, both included. */
export function daysBetween(first: string, last: string): number {
  return Math.round((Date.parse(last) - Date.parse(first)) / 86_400_000) + 1;
}

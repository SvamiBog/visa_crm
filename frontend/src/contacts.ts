import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./api";
import type { BookingType, IdentifierType } from "./reference";

// Contacts (backend/app/api/contacts.py). Search goes in a POST body: names and phones
// are personal data and must not appear in URLs.

export const MESSENGERS = ["whatsapp", "viber"] as const;
export type Messenger = (typeof MESSENGERS)[number];

export type Contact = {
  id: string;
  full_name: string;
  full_name_latin: string | null;
  phone: string | null;
  telegram: string | null;
  messenger: Messenger | null;
  messenger_contact: string | null;
  email: string | null;
  birth_date: string | null;
  lead_channel_id: string | null;
  partner_id: string | null;
  consent_signed_on: string | null;
  contract_number: string | null;
  contract_signed_on: string | null;
  created_at: string;
  updated_at: string;
};

export type ContactListItem = Pick<
  Contact,
  "id" | "full_name" | "full_name_latin" | "phone" | "telegram" | "lead_channel_id" | "created_at"
>;

export type Note = {
  id: string;
  body: string;
  author_id: string | null;
  author_email: string | null;
  created_at: string;
  edited_at: string | null;
};

export type Relation = { related_id: string; full_name: string; label: string | null };

export const DECISIONS = ["yes", "no", "thinking"] as const;
export type Decision = (typeof DECISIONS)[number];

export type Consultation = {
  id: string;
  contact_id: string;
  held_on: string;
  month: string;
  applicants: number;
  decision: Decision | null;
  lead_channel_id: string | null;
  destination_ids: string[];
  trip_planned_on: string | null;
  fee_amount: string | null;
  fee_currency: string | null;
  fee_paid: boolean;
  fee_paid_on: string | null;
  notes: string | null;
  created_at: string;
};

export const VISA_KINDS = ["schengen", "national", "uk", "usa", "asia", "other"] as const;
export type VisaRecord = {
  kind: (typeof VISA_KINDS)[number];
  country: string | null;
  valid_from: string | null;
  valid_to: string | null;
  note: string | null;
};
export type Refusal = { country: string; year: number | null; reason: string | null };

/** Questionnaire answers kept on the contact (backend/app/core/questionnaire.py). */
export type Questionnaire = {
  passport_issued_on: string | null;
  passport_expires_on: string | null;
  visas: VisaRecord[];
  relatives_abroad: string[];
  refusals: Refusal[];
  deportation: boolean;
  deportation_note: string | null;
  humanitarian_trips: string[];
  note: string | null;
};

export type Hint = { code: string; country: string | null };

// Cases (backend/app/api/cases.py): one visa for one applicant; a family applying
// together is a submission group of several cases.

export const CASE_STAGES = ["waiting", "documents", "submission", "review", "result"] as const;
export type CaseStage = (typeof CASE_STAGES)[number];
export const OUTCOMES = ["visa", "refusal", "not_submitted"] as const;
export type Outcome = (typeof OUTCOMES)[number];
export const VISA_ENTRIES = ["single", "double", "multiple"] as const;
export type VisaEntries = (typeof VISA_ENTRIES)[number];
export const HALLS = ["standard", "premium"] as const;
export type Hall = (typeof HALLS)[number];

export type GroupMember = {
  case_id: string;
  number: number;
  contact_id: string;
  full_name: string;
  destination_id: string;
  stage: CaseStage;
};

export const EMAIL_OWNERS = ["client", "specialist", "specialist_backup", "registrar"] as const;
export type EmailOwner = (typeof EMAIL_OWNERS)[number];

export type QueueStatus = "in_queue" | "date_received" | "submitted" | "removed";
export type QueueEntry = {
  id: string;
  case_id: string;
  queued_on: string;
  letter_on: string | null;
  removed_on: string | null;
  removed_reason: string | null;
  status: QueueStatus;
  days_waiting: number;
};

export type AttemptStatus = "open" | "date_received" | "expired" | "cancelled";
export type Attempt = {
  id: string;
  case_id: string;
  number: number;
  sent_on: string;
  deadline_on: string;
  outcome: Exclude<AttemptStatus, "open"> | null;
  closed_on: string | null;
  status: AttemptStatus;
  days_left: number | null;
};

export type VisaCase = {
  id: string;
  number: number;
  contact_id: string;
  group_id: string | null;
  consultation_id: string | null;
  destination_id: string;
  purpose_id: string | null;
  submission_country_id: string;
  visa_center_id: string | null;
  registrar_id: string | null;
  hall: Hall | null;
  stage: CaseStage;
  submission_on: string | null;
  outcome: Outcome | null;
  decided_on: string | null;
  visa_entries: VisaEntries | null;
  visa_valid_from: string | null;
  visa_valid_to: string | null;
  stay_days: number | null;
  after_appeal: boolean;
  created_at: string;
  group: GroupMember[];
  appeals_accepted: boolean | null;
  appeal_until: string | null;
  vac_number: string | null;
  application_email: string | null;
  application_email_owner: EmailOwner | null;
  booking_type: BookingType | null;
  identifier_type: IdentifierType | null;
  window_days: number | null;
  queue_entries: QueueEntry[];
  attempts: Attempt[];
  average_wait_days: number | null;
  bookings: Booking[];
};

export const BOOKING_KINDS = ["hotel", "flight"] as const;
export type BookingKind = (typeof BOOKING_KINDS)[number];
export const BOOKING_STATUSES = ["active", "rescheduled", "cancelled"] as const;
export type BookingStatus = (typeof BOOKING_STATUSES)[number];
export type BookingWarning = "case_closed" | "cancellation_passed" | "expires_before_submission";
/** A hotel or the client's flight booking for the document package (backlog 1.8). */
export type Booking = {
  id: string;
  case_id: string;
  kind: BookingKind;
  status: BookingStatus;
  place: string;
  starts_on: string | null;
  ends_on: string | null;
  reference: string | null;
  free_cancel_until: string | null;
  valid_until: string | null;
  vault_entry_id: string | null;
  note: string | null;
  deadline: string | null;
  days_left: number | null;
  warnings: BookingWarning[];
};

export const DOCUMENT_KINDS = ["consent", "contract"] as const;
export type DocumentKind = (typeof DOCUMENT_KINDS)[number];

/** A file of the signed consent or contract (backend/app/api/documents.py). */
export type ContactDocument = {
  id: string;
  kind: DocumentKind;
  file_name: string;
  content_type: string;
  size_bytes: number;
  uploaded_by: string | null;
  uploaded_by_email: string | null;
  created_at: string;
};

export type ContactCard = {
  contact: Contact;
  notes: Note[];
  relations: Relation[];
  consultations: Consultation[];
  questionnaire: Questionnaire;
  hints: Hint[];
  /** null when the user has no «cases» module. */
  cases: VisaCase[] | null;
  /** Newest first. */
  documents: ContactDocument[];
};

// Client index (backend/app/api/client_status.py): every contact is in one tab.

export const TABS = ["all", "work", "consult", "thinking", "archive"] as const;
export type Tab = (typeof TABS)[number];

export type CaseBrief = {
  id: string;
  number: number;
  destination_id: string;
  purpose_id: string | null;
  stage: CaseStage;
  outcome: Outcome | null;
  submission_on: string | null;
  group_size: number;
};

export type ContactRow = ContactListItem & {
  tab: Exclude<Tab, "all">;
  case: CaseBrief | null;
  cases_in_work: number;
  consultation: { held_on: string; decision: Decision | null; destination_ids: string[] } | null;
};

export type SearchPage = { items: ContactRow[]; has_more: boolean; counts: Record<Tab, number> };

export type BoardCase = {
  id: string;
  number: number;
  contact_id: string;
  full_name: string;
  group_id: string | null;
  destination_id: string;
  purpose_id: string | null;
  registrar_id: string | null;
  stage: CaseStage;
  outcome: Outcome | null;
  submission_on: string | null;
  decided_on: string | null;
};

export type BoardLead = {
  contact_id: string;
  full_name: string;
  tab: "consult" | "thinking";
  held_on: string | null;
  decision: Decision | null;
  destination_ids: string[];
};

export type Board = { cases: BoardCase[]; leads: BoardLead[] };

export type Entry = { id: string; name: string; is_active: boolean };
export type PartnerRef = { id: string; name: string; is_active: boolean };

export const contactKey = (id: string) => ["contacts", id] as const;

/** Lead channels and partners for the contact form (the specialist keeps them in Lists). */
export function useContactLists() {
  const channels = useQuery({
    queryKey: ["dictionaries", "channels"],
    queryFn: () => apiGet<Entry[]>("/dictionaries/channels"),
  });
  const partners = useQuery({
    queryKey: ["dictionaries", "partners"],
    queryFn: () => apiGet<PartnerRef[]>("/dictionaries/partners"),
  });
  return { channels: channels.data ?? [], partners: partners.data ?? [] };
}

/** Visa purposes and registrars for the case form. */
export function useCaseLists() {
  const purposes = useQuery({
    queryKey: ["dictionaries", "purposes"],
    queryFn: () => apiGet<Entry[]>("/dictionaries/purposes"),
  });
  const registrars = useQuery({
    queryKey: ["dictionaries", "registrars"],
    queryFn: () => apiGet<PartnerRef[]>("/dictionaries/registrars"),
  });
  return { purposes: purposes.data ?? [], registrars: registrars.data ?? [] };
}

/** "+375291234567" → "+375 29 123-45-67" for Belarus; other numbers in groups of 3. */
export function formatPhone(phone: string | null): string {
  if (!phone) return "";
  const by = /^\+375(\d{2})(\d{3})(\d{2})(\d{2})$/.exec(phone);
  if (by) return `+375 ${by[1]} ${by[2]}-${by[3]}-${by[4]}`;
  return phone.replace(/^(\+\d{1,3})(\d+)$/, (_, code: string, rest: string) =>
    `${code} ${rest.replace(/(\d{3})(?=\d)/g, "$1 ")}`,
  );
}

/** A Telegram username opens the chat; a display name cannot be linked. */
export function telegramUrl(telegram: string | null): string | null {
  return telegram && /^[A-Za-z0-9_]{5,32}$/.test(telegram) ? `https://t.me/${telegram}` : null;
}

/** ISO date ("1990-10-14") shown in the interface language, without time-zone shifts. */
export function formatDate(value: string | null, language: string): string {
  if (!value) return "";
  const [y, m, d] = value.split("-").map(Number);
  return new Intl.DateTimeFormat(language, { dateStyle: "medium", timeZone: "UTC" }).format(
    new Date(Date.UTC(y, m - 1, d)),
  );
}

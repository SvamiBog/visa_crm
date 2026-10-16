import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./api";

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

export type ContactCard = { contact: Contact; notes: Note[]; relations: Relation[] };

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

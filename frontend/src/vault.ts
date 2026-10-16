import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./api";
import type { EmailOwner } from "./contacts";

// The vault of accounts (backend/app/api/vault.py). Passwords never live in the query
// cache: they come only from an explicit «show» and stay in the component's state.

export const SYSTEM_KINDS = ["email", "visa_center", "embassy", "booking", "other"] as const;
export type SystemKind = (typeof SYSTEM_KINDS)[number];

export type VaultSystem = {
  id: string;
  name: string;
  kind: SystemKind;
  url: string | null;
  password_length: number;
  password_lower: boolean;
  password_upper: boolean;
  password_digits: boolean;
  password_symbols: string;
  sort_order: number;
  is_active: boolean;
};

export type VaultEntry = {
  id: string;
  system_id: string;
  contact_id: string | null;
  case_id: string | null;
  login: string;
  email_owner: EmailOwner | null;
  has_password: boolean;
  password_updated_at: string | null;
  created_manually: boolean;
  extra_id: string | null;
  note: string | null;
  created_at: string;
  updated_at: string;
};

export type AccountOnMailbox = {
  id: string;
  system_id: string;
  contact_id: string;
  contact_name: string;
};
export type OwnVaultEntry = VaultEntry & { accounts: AccountOnMailbox[] };

export const SYSTEMS_KEY = ["dictionaries", "vault-systems"] as const;
/** A client's accounts, or the workspace's own (null). */
export const vaultKey = (contactId: string | null) => ["vault", contactId ?? "own"] as const;

export function useVaultSystems() {
  return useQuery({
    queryKey: SYSTEMS_KEY,
    queryFn: () => apiGet<VaultSystem[]>("/dictionaries/vault-systems"),
  });
}

export function useOwnVault(enabled = true) {
  return useQuery({
    queryKey: vaultKey(null),
    queryFn: () => apiGet<OwnVaultEntry[]>("/vault/own"),
    enabled,
  });
}

/** The site's rules in short: "12 · a–z A–Z 0–9 !@#". */
export function rulesSummary(s: VaultSystem): string {
  return [
    String(s.password_length),
    [
      s.password_lower && "a–z",
      s.password_upper && "A–Z",
      s.password_digits && "0–9",
      s.password_symbols,
    ]
      .filter(Boolean)
      .join(" "),
  ].join(" · ");
}

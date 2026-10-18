import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./api";

// Document templates (backlog 2.1, backend/app/api/templates.py): the specialist's Word
// files with codes, given to a client and downloaded filled with the client's data.

export type DocumentTemplate = {
  id: string;
  name: string;
  // The filled file's name with codes; null: «<name> — <client>».
  file_name: string | null;
  source_name: string;
  size_bytes: number;
  // Keys of the known codes found in the file.
  codes: string[];
  // Codes that do not exist, as written in the file: typos.
  unknown_codes: string[];
  is_active: boolean;
  clients: number;
  created_at: string;
  updated_at: string;
};

export type TemplateCode = { key: string; group: string; ru: string; en: string };

/** A template given to a client. */
export type GivenTemplate = {
  id: string;
  template_id: string;
  name: string;
  case_id: string | null;
  file_name: string;
  missing: string[];
  unknown_codes: string[];
  template_active: boolean;
  created_at: string;
};

export const MAX_TEMPLATE_BYTES = 5 * 1024 * 1024;
export const DOCX_TYPES =
  ".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document";
export const TEMPLATES_KEY = ["templates"] as const;

export function useTemplates() {
  return useQuery({
    queryKey: TEMPLATES_KEY,
    queryFn: () => apiGet<DocumentTemplate[]>("/templates"),
  });
}

export function useTemplateCodes() {
  return useQuery({
    queryKey: [...TEMPLATES_KEY, "codes"],
    queryFn: () => apiGet<TemplateCode[]>("/templates/codes"),
    staleTime: Infinity,
  });
}

/** How a code is written in the interface's language: {{ФИО}} or {{Full name}}. */
export function codeText(code: TemplateCode | undefined, language: string): string {
  if (!code) return "";
  return `{{${language.startsWith("ru") ? code.ru : code.en}}}`;
}

export function givenKey(contactId: string) {
  return [...TEMPLATES_KEY, "contact", contactId] as const;
}

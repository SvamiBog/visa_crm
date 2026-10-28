import { useQuery } from "@tanstack/react-query";
import { apiDownload, apiGet } from "./api";

// Импорт в формате VisaCRM (backlog 1.21, docs/data-format.md): the template and the
// prompt for the AI, the check and the import of the workbook it makes, the history.

// docs/data-format.md: one XLSX file up to 10 MB.
export const MAX_IMPORT_BYTES = 10 * 1024 * 1024;
// Lists an unknown value may be added to; a country, a city or a system is fixed in the file.
export const CREATABLE = ["channel", "partner", "purpose", "registrar"];

export type PromptLanguage = "ru" | "en";
// A line of the report: where and why, never the cell's value. Runs of the old import had
// one sheet and no sheet name.
export type ImportIssue = {
  sheet: string | null;
  row: number | null;
  column: string | null;
  reason: string;
};
export type UnknownValue = { key: string; value: string; rows: number };
// An unknown list value: type → normalized value → "new", "skip" or an entry's id.
export type ImportChoices = Record<string, Record<string, string>>;
export type ImportReport = {
  id: string | null;
  kind: string;
  file_name: string;
  created_at: string | null;
  rows: number;
  imported: number;
  skipped: ImportIssue[];
  skipped_count: number;
  warnings: ImportIssue[];
  warnings_count: number;
  created: Record<string, number>;
  updated: Record<string, number>;
  unknown: Record<string, UnknownValue[]>;
};
export type ImportRunSummary = {
  id: string;
  kind: string;
  file_name: string;
  rows: number;
  imported: number;
  skipped_count: number;
  warnings_count: number;
  created_by: string | null;
  created_at: string;
};

export function promptLanguage(language: string | undefined): PromptLanguage {
  return language?.startsWith("en") ? "en" : "ru";
}

export function useImportRuns() {
  return useQuery({ queryKey: ["imports"], queryFn: () => apiGet<ImportRunSummary[]>("/imports") });
}

export function useImportReport(id: string | null) {
  return useQuery({
    queryKey: ["imports", id],
    queryFn: () => apiGet<ImportReport>(`/imports/${id}`),
    enabled: id !== null,
  });
}

export function fetchImportPrompt(language: PromptLanguage): Promise<string> {
  return apiGet<{ text: string }>(`/imports/prompt?language=${language}`).then((p) => p.text);
}

export function downloadImportTemplate(language: PromptLanguage, fileName: string): Promise<void> {
  return apiDownload(`/imports/template?language=${language}`, fileName);
}

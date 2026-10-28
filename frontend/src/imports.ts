import { useQuery } from "@tanstack/react-query";
import { apiDownload, apiGet, apiPost } from "./api";

// Импорт в формате VisaCRM (backlog 1.21, docs/data-format.md): the template and the
// prompt for the AI, the check and the import of the workbook it makes, the history.
// The check and the import run on the server in the background: the request starts a job,
// the page asks how far it is (a request held open for minutes was cut on the way).

// docs/data-format.md: one XLSX file up to 10 MB.
export const MAX_IMPORT_BYTES = 10 * 1024 * 1024;
// Lists an unknown value may be added to; a country, a city or a system is fixed in the file.
export const CREATABLE = ["channel", "partner", "purpose", "registrar"];
// How often the page asks how far a running job is.
const POLL_MS = 1500;

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

export type ImportJob = {
  id: string;
  kind: "check" | "import";
  file_name: string;
  // "stopped": a newer check of the workspace replaced it.
  status: "running" | "done" | "failed" | "stopped";
  rows_done: number;
  rows_total: number;
  report: ImportReport | null;
  error: string | null;
};
export type ImportUpload = { name: string; content: string };

export function startImportJob(
  kind: ImportJob["kind"],
  file: ImportUpload,
  choices: ImportChoices,
): Promise<ImportJob> {
  return apiPost<ImportJob>(kind === "check" ? "/imports/check" : "/imports", {
    file_name: file.name,
    content: file.content,
    choices,
  });
}

/** A job, asked again while it runs. A phone asleep pauses the asking; it goes on, with
 * the job still running on the server, when the page is back. */
export function useImportJob(id: string | null) {
  return useQuery({
    queryKey: ["import-jobs", id],
    queryFn: () => apiGet<ImportJob>(`/imports/jobs/${id}`),
    enabled: id !== null,
    refetchInterval: (query) => (query.state.data?.status === "running" ? POLL_MS : false),
  });
}

/** The workspace's job still running: one started before the page was opened again. */
export function useRunningImportJobs() {
  return useQuery({
    queryKey: ["import-jobs"],
    queryFn: () => apiGet<ImportJob[]>("/imports/jobs"),
    refetchInterval: (query) => (query.state.data?.length ? POLL_MS : false),
  });
}

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

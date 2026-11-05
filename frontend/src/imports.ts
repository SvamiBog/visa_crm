import { useQuery } from "@tanstack/react-query";
import { apiDelete, apiDownload, apiGet, apiPost } from "./api";

// Импорт в формате VisaCRM (backlog 1.21, 1.22, docs/data-format.md): the template and the
// prompt for the AI, then the workbook it makes. The file is uploaded once and waits on the
// server: a few sample cards, the check of the whole file with every card, and the import
// only once the specialist confirms it. The sample, the check and the import run on the
// server in the background: the request starts a job, the page asks how far it is (a
// request held open for minutes was cut on the way).

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
  // Only the import writes: the sample and the check show what it would do.
  kind: "sample" | "check" | "import";
  file_name: string;
  upload_id: string | null;
  // "stopped": a newer sample or check of the workspace replaced it.
  status: "running" | "done" | "failed" | "stopped";
  rows_done: number;
  rows_total: number;
  report: ImportReport | null;
  error: string | null;
};
// The file waiting on the server and how far the specialist has come with it.
export type ImportUpload = {
  id: string;
  file_name: string;
  size: number;
  sheets: { title: string; rows: number }[];
  clients: number;
  sampled: boolean;
  checked: boolean;
  report: ImportReport | null;
};

// A card before the import: sections by the format's sheets, fields with its headers in
// the interface's language. A password is only "secret": that there is one.
export type CardField = {
  key: string;
  label: string;
  type: "text" | "date" | "money" | "number" | "bool" | "secret";
  value: string | boolean;
};
export type CardRecord = {
  change: "new" | "updated" | null;
  fields: CardField[];
  sections: CardSection[];
};
export type CardSection = { sheet: string; title: string; records: CardRecord[] };
export type CardStatus = "new" | "updated" | "same";
// name null: the specialist's own accounts.
export type ImportCard = {
  n: number;
  status: CardStatus;
  name: string | null;
  phone: string | null;
  sections: CardSection[];
  issues: ImportIssue[];
};
export type ImportCardLine = {
  n: number;
  status: CardStatus;
  name: string | null;
  phone: string | null;
  consultations: number;
  cases: number;
  issues: number;
};

export function uploadImportFile(name: string, content: string): Promise<ImportUpload> {
  return apiPost<ImportUpload>("/imports/uploads", { file_name: name, content });
}

export function cancelImportUpload(id: string): Promise<void> {
  return apiDelete<void>(`/imports/uploads/${id}`);
}

/** The file waiting on the server: the page opened again goes on where it was. */
export function useImportUpload() {
  return useQuery({
    queryKey: ["import-upload"],
    queryFn: () => apiGet<ImportUpload | null>("/imports/uploads/current"),
  });
}

export function startImportJob(
  kind: ImportJob["kind"],
  uploadId: string,
  choices: ImportChoices,
): Promise<ImportJob> {
  return apiPost<ImportJob>(`/imports/uploads/${uploadId}/${kind}`, { choices });
}

export function useSampleCards(uploadId: string | null, language: PromptLanguage) {
  return useQuery({
    queryKey: ["import-sample", uploadId, language],
    queryFn: () => apiGet<ImportCard[]>(`/imports/uploads/${uploadId}/sample?language=${language}`),
    enabled: uploadId !== null,
  });
}

export function useImportCards(uploadId: string | null) {
  return useQuery({
    queryKey: ["import-cards", uploadId],
    queryFn: () => apiGet<ImportCardLine[]>(`/imports/uploads/${uploadId}/cards`),
    enabled: uploadId !== null,
  });
}

/** One card by its place in the list: no client's data in the address. */
export function useImportCard(uploadId: string, n: number, language: PromptLanguage) {
  return useQuery({
    queryKey: ["import-cards", uploadId, n, language],
    queryFn: () =>
      apiGet<ImportCard>(`/imports/uploads/${uploadId}/cards/${n}?language=${language}`),
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

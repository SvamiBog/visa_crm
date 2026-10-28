import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiPost } from "../../api";
import { useCaseLists, useContactLists, type Entry } from "../../contacts";
import { readBase64 } from "../../files";
import {
  CREATABLE,
  MAX_IMPORT_BYTES,
  downloadImportTemplate,
  fetchImportPrompt,
  promptLanguage,
  useImportReport,
  useImportRuns,
  type ImportChoices,
  type ImportIssue,
  type ImportReport,
  type ImportRunSummary,
} from "../../imports";
import { ErrorAlert } from "../clients/ui";

// «Импорт таблиц» (backlog 1.21, docs/data-format.md): the template and the prompt for
// the AI, then the workbook it makes — checked first, loaded after. The file stays in the
// browser and is sent again for the check and the import.

// The order counts come in: people first, then what hangs on them.
const COUNT_ORDER = [
  "contacts",
  "notes",
  "consultations",
  "cases",
  "groups",
  "queue_entries",
  "attempts",
  "bookings",
  "finances",
  "payments",
  "expenses",
  "vault_entries",
  "passwords",
  "channel",
  "partner",
  "purpose",
  "registrar",
];
const SHOWN_ISSUES = 30;
type Upload = { name: string; content: string };

export function ImportPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [upload, setUpload] = useState<Upload | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [choices, setChoices] = useState<ImportChoices>({});
  const [checked, setChecked] = useState<ImportReport | null>(null);
  const [result, setResult] = useState<ImportReport | null>(null);
  // A new key clears the file input for the next file.
  const [inputKey, setInputKey] = useState(0);
  const runs = useImportRuns();

  const body = (file: Upload, picked: ImportChoices) => ({
    file_name: file.name,
    content: file.content,
    choices: picked,
  });
  const checking = useMutation({
    mutationFn: (request: { file: Upload; choices: ImportChoices }) =>
      apiPost<ImportReport>("/imports/check", body(request.file, request.choices)),
    onSuccess: setChecked,
  });
  const importing = useMutation({
    mutationFn: (file: Upload) => apiPost<ImportReport>("/imports", body(file, choices)),
    onSuccess: (report) => {
      setResult(report);
      setChecked(null);
      // Cards, cases, queues and lists all may have changed.
      void queryClient.invalidateQueries();
    },
  });

  const chooseFile = async (picked: File | undefined) => {
    setChecked(null);
    setResult(null);
    setChoices({});
    setUpload(null);
    setFileError(null);
    if (!picked) return;
    if (picked.size > MAX_IMPORT_BYTES) {
      setFileError("file_too_large");
      return;
    }
    const next = { name: picked.name.slice(-200), content: await readBase64(picked) };
    setUpload(next);
    checking.mutate({ file: next, choices: {} });
  };

  const choose = (type: string, key: string, value: string) => {
    setChoices({ ...choices, [type]: { ...(choices[type] ?? {}), [key]: value } });
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("import.title")}</h1>
          <div className="muted">{t("import.subtitle")}</div>
        </div>
      </header>

      <Prepare />

      <section className="card import-step">
        <h2 className="h2">{t("import.stepFile")}</h2>
        <div className="form-grid import-file">
          <label className="field">
            <span>{t("import.file")}</span>
            <input
              key={inputKey}
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(e) => void chooseFile(e.target.files?.[0])}
            />
            <small className="muted">{t("import.fileHint")}</small>
          </label>
        </div>
        {fileError && (
          <div className="alert alert-error" role="alert">
            {t(`errors.${fileError}`)}
          </div>
        )}
        {checking.isPending && <p className="muted">{t("import.checking")}</p>}
        {checking.isError && <ErrorAlert error={checking.error} />}
      </section>

      {checked && upload && !result && (
        <section className="card import-step">
          <h2 className="h2">{t("import.stepCheck")}</h2>
          <p className="muted import-note">{t("import.checkNote")}</p>
          <ReportView report={checked} dryRun />
          <UnknownValues report={checked} choices={choices} onChange={choose} />
          <div className="form-actions">
            <button
              type="button"
              className="btn btn-p"
              disabled={importing.isPending || checked.imported === 0}
              onClick={() => importing.mutate(upload)}
            >
              {importing.isPending
                ? t("import.importing")
                : t("import.run", { count: checked.imported })}
            </button>
            <button
              type="button"
              className="btn"
              disabled={checking.isPending}
              onClick={() => checking.mutate({ file: upload, choices })}
            >
              {t("import.checkAgain")}
            </button>
          </div>
          {importing.isError && <ErrorAlert error={importing.error} />}
        </section>
      )}

      {result && (
        <section className="card import-step">
          <h2 className="h2">{t("import.done")}</h2>
          <ReportView report={result} />
          {(result.created.passwords ?? 0) > 0 && (
            <div className="alert alert-warn import-note" role="status">
              {t("import.passwordsMoved", { count: result.created.passwords })}
            </div>
          )}
          <div className="form-actions">
            <Link className="btn btn-p" to="/app/clients">
              {t("import.toClients")}
            </Link>
            <button
              type="button"
              className="btn"
              onClick={() => {
                setResult(null);
                setUpload(null);
                setInputKey(inputKey + 1);
              }}
            >
              {t("import.another")}
            </button>
          </div>
        </section>
      )}

      <History runs={runs.data} error={runs.isError ? runs.error : null} />
    </div>
  );
}

/** The first step: the template and the prompt the specialist gives the AI. */
function Prepare() {
  const { t, i18n } = useTranslation();
  const language = promptLanguage(i18n.resolvedLanguage);
  const [prompt, setPrompt] = useState<string | null>(null);
  const [shown, setShown] = useState(false);
  const [copied, setCopied] = useState(false);
  const loading = useMutation({ mutationFn: () => fetchImportPrompt(language) });
  const downloading = useMutation({
    mutationFn: () => downloadImportTemplate(language, t("import.templateFile")),
  });

  const text = async (): Promise<string> => {
    if (prompt !== null) return prompt;
    const loaded = await loading.mutateAsync();
    setPrompt(loaded);
    return loaded;
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(await text());
      setCopied(true);
    } catch {
      // No clipboard (an old browser, no permission) or no prompt: it is shown to copy by
      // hand, or the error of loading it is.
      setCopied(false);
      setShown(true);
    }
  };
  const toggle = async () => {
    try {
      if (!shown) await text();
      setShown(!shown);
    } catch {
      // The error of loading the prompt is shown below the buttons.
    }
  };

  return (
    <section className="card import-step">
      <h2 className="h2">{t("import.stepPrepare")}</h2>
      <ol className="import-how">
        <li>{t("import.prepareTemplate")}</li>
        <li>{t("import.preparePrompt")}</li>
        <li>{t("import.prepareFiles")}</li>
      </ol>
      <div className="alert alert-warn import-note" role="note">
        {t("import.privacy")}
      </div>
      <div className="form-actions">
        <button
          type="button"
          className="btn btn-p"
          disabled={downloading.isPending}
          onClick={() => downloading.mutate()}
        >
          {t("import.downloadTemplate")}
        </button>
        <button type="button" className="btn" onClick={() => void copy()}>
          {copied ? t("import.promptCopied") : t("import.copyPrompt")}
        </button>
        <button
          type="button"
          className="link-button"
          aria-expanded={shown}
          onClick={() => void toggle()}
        >
          {shown ? t("import.hidePrompt") : t("import.showPrompt")}
        </button>
      </div>
      {downloading.isError && <ErrorAlert error={downloading.error} />}
      {loading.isError && <ErrorAlert error={loading.error} />}
      {shown && prompt !== null && (
        <textarea
          className="import-prompt"
          aria-label={t("import.promptLabel")}
          readOnly
          rows={14}
          value={prompt}
        />
      )}
    </section>
  );
}

function ReportView({ report, dryRun = false }: { report: ImportReport; dryRun?: boolean }) {
  const { t } = useTranslation();
  const keys = COUNT_ORDER.filter((k) => report.created[k] || report.updated[k]);
  return (
    <div className="import-report">
      <div className="kpis">
        <Kpi label={t("import.rows")} value={report.rows} />
        <Kpi label={t(dryRun ? "import.willImport" : "import.imported")} value={report.imported} />
        <Kpi label={t("import.skipped")} value={report.skipped_count} />
        <Kpi label={t("import.warnings")} value={report.warnings_count} />
      </div>
      {keys.length > 0 ? (
        <div className="table-wrap">
          <table className="tbl import-counts">
            <thead>
              <tr>
                <th>{t("import.what")}</th>
                <th className="money-cell">{t(dryRun ? "import.willCreate" : "import.created")}</th>
                <th className="money-cell">{t(dryRun ? "import.willUpdate" : "import.updated")}</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k}>
                  <td>{t(`import.count.${k}`)}</td>
                  <td className="money-cell">{report.created[k] ?? "—"}</td>
                  <td className="money-cell">{report.updated[k] ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">{t("import.nothingChanges")}</p>
      )}
      <Issues title={t("import.skippedRows")} issues={report.skipped} total={report.skipped_count} />
      <Issues title={t("import.warningRows")} issues={report.warnings} total={report.warnings_count} />
      {dryRun && report.skipped.length + report.warnings.length > 0 && <CopyForAi report={report} />}
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: number }) {
  return (
    <div className="kpi">
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
    </div>
  );
}

/** «Лист «Кейсы», строка 7, колонка «Дата подачи»» — never the cell's value. */
function useIssueText() {
  const { t } = useTranslation();
  return (issue: ImportIssue) => {
    const where = [
      issue.sheet ? t("import.whereSheet", { sheet: issue.sheet }) : null,
      issue.row !== null ? t("import.whereRow", { row: issue.row }) : null,
      issue.column ? t("import.whereColumn", { column: issue.column }) : null,
    ].filter(Boolean);
    const reason = t(`import.reason.${issue.reason}`, { defaultValue: t("import.reason.other") });
    return where.length ? `${where.join(", ")}: ${reason}` : reason;
  };
}

function Issues({ title, issues, total }: { title: string; issues: ImportIssue[]; total: number }) {
  const { t } = useTranslation();
  const text = useIssueText();
  const [all, setAll] = useState(false);
  if (issues.length === 0) return null;
  const shown = all ? issues : issues.slice(0, SHOWN_ISSUES);
  return (
    <div className="import-issues">
      <h3 className="h3">{title}</h3>
      <ul className="plain-list import-issue-list">
        {shown.map((issue, i) => (
          <li key={`${issue.sheet}-${issue.row}-${issue.column ?? ""}-${i}`}>{text(issue)}</li>
        ))}
      </ul>
      {issues.length > SHOWN_ISSUES && (
        <button type="button" className="link-button" onClick={() => setAll(!all)}>
          {all ? t("import.showLess") : t("import.showAll", { count: issues.length })}
        </button>
      )}
      {total > issues.length && (
        <p className="muted small">{t("import.moreIssues", { count: total - issues.length })}</p>
      )}
    </div>
  );
}

/** The check's report as text for the AI that made the file: it fixes the file by it. */
function CopyForAi({ report }: { report: ImportReport }) {
  const { t } = useTranslation();
  const text = useIssueText();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    const lines = [
      t("import.reportForAi", { file: report.file_name }),
      ...[...report.skipped, ...report.warnings].map((issue) => `- ${text(issue)}`),
      ...Object.entries(report.unknown).flatMap(([type, values]) =>
        values.map((v) => `- ${t(`import.listName.${type}`)}: «${v.value}» — ${t("import.notInLists")}`),
      ),
    ];
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div>
      <button type="button" className="btn" onClick={() => void copy()}>
        {copied ? t("import.reportCopied") : t("import.copyReport")}
      </button>
      <p className="muted small">{t("import.copyReportHint")}</p>
    </div>
  );
}

function UnknownValues({
  report,
  choices,
  onChange,
}: {
  report: ImportReport;
  choices: ImportChoices;
  onChange: (type: string, key: string, value: string) => void;
}) {
  const { t } = useTranslation();
  const { channels, partners } = useContactLists();
  const { purposes, registrars } = useCaseLists();
  const lists: Record<string, Entry[]> = {
    channel: channels,
    partner: partners,
    purpose: purposes,
    registrar: registrars,
  };
  const types = Object.keys(report.unknown).filter((type) => report.unknown[type].length > 0);
  if (types.length === 0) return null;
  return (
    <div className="import-unknown">
      <h3 className="h3">{t("import.unknownTitle")}</h3>
      <p className="muted small">{t("import.unknownNote")}</p>
      <div className="table-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>{t("import.list")}</th>
              <th>{t("import.value")}</th>
              <th className="money-cell">{t("import.rowsCount")}</th>
              <th>{t("import.action")}</th>
            </tr>
          </thead>
          <tbody>
            {types.flatMap((type) =>
              report.unknown[type].map((item) => (
                <tr key={`${type}-${item.key}`}>
                  <td className="muted">{t(`import.listName.${type}`)}</td>
                  <td>{item.value}</td>
                  <td className="money-cell">{item.rows}</td>
                  <td>
                    {CREATABLE.includes(type) ? (
                      <select
                        aria-label={t("import.actionFor", { value: item.value })}
                        value={choices[type]?.[item.key] ?? "new"}
                        onChange={(e) => onChange(type, item.key, e.target.value)}
                      >
                        <option value="new">{t("import.createNew")}</option>
                        <option value="skip">{t("import.leaveEmpty")}</option>
                        {(lists[type] ?? [])
                          .filter((entry) => entry.is_active)
                          .map((entry) => (
                            <option key={entry.id} value={entry.id}>
                              {entry.name}
                            </option>
                          ))}
                      </select>
                    ) : (
                      <span className="muted small">{t("import.fixInFile")}</span>
                    )}
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function History({ runs, error }: { runs: ImportRunSummary[] | undefined; error: unknown }) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState<string | null>(null);
  const report = useImportReport(open);
  const when = (value: string) =>
    new Intl.DateTimeFormat(i18n.language, { dateStyle: "short", timeStyle: "short" }).format(
      new Date(value),
    );
  return (
    <section className="card">
      <h2 className="h2">{t("import.history")}</h2>
      {error ? <ErrorAlert error={error} /> : null}
      {runs && runs.length === 0 && <p className="muted">{t("import.noRuns")}</p>}
      {runs && runs.length > 0 && (
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>{t("import.when")}</th>
                <th>{t("import.fileName")}</th>
                <th className="money-cell">{t("import.imported")}</th>
                <th className="money-cell">{t("import.skipped")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td className="nowrap">
                    {when(run.created_at)}
                    {run.created_by && <div className="muted small">{run.created_by}</div>}
                  </td>
                  <td>
                    {run.file_name}
                    {run.kind !== "workbook" && (
                      <div className="muted small">{t("import.oldImport")}</div>
                    )}
                  </td>
                  <td className="money-cell">
                    {run.imported} / {run.rows}
                  </td>
                  <td className="money-cell">{run.skipped_count}</td>
                  <td>
                    <button
                      type="button"
                      className="link-button"
                      aria-expanded={open === run.id}
                      onClick={() => setOpen(open === run.id ? null : run.id)}
                    >
                      {open === run.id ? t("import.hideReport") : t("import.showReport")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && report.isPending && <p className="muted">{t("common.loading")}</p>}
      {open && report.data && <ReportView report={report.data} />}
    </section>
  );
}

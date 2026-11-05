import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useCaseLists, useContactLists, type Entry } from "../../contacts";
import { formatSize, readBase64 } from "../../files";
import {
  CREATABLE,
  MAX_IMPORT_BYTES,
  cancelImportUpload,
  downloadImportTemplate,
  fetchImportPrompt,
  promptLanguage,
  startImportJob,
  uploadImportFile,
  useImportJob,
  useImportReport,
  useImportRuns,
  useImportUpload,
  useRunningImportJobs,
  useSampleCards,
  type ImportChoices,
  type ImportIssue,
  type ImportJob,
  type ImportReport,
  type ImportRunSummary,
  type ImportUpload,
} from "../../imports";
import { ErrorAlert } from "../clients/ui";
import { CardList, CardView, useIssueText } from "./ImportCard";

// «Импорт таблиц» (backlog 1.21, 1.22, docs/data-format.md): the template and the prompt for
// the AI, then the workbook it makes. The file is uploaded once and waits on the server: a
// few sample cards first, then the check of the whole file with every card it will make,
// and only when the specialist confirms is it loaded into the CRM. The runs go on on the
// server in the background; the page shows how far they are and, opened again, goes on
// where it was.

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
type Kind = ImportJob["kind"];

export function ImportPage() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const upload = useImportUpload();
  const current = upload.data ?? null;
  const [fileError, setFileError] = useState<string | null>(null);
  const [choices, setChoices] = useState<ImportChoices>({});
  // The runs of the file this page follows, by kind.
  const [jobs, setJobs] = useState<Partial<Record<Kind, string>>>({});
  // A new key clears the file input for the next file.
  const [inputKey, setInputKey] = useState(0);
  const runs = useImportRuns();
  const running = useRunningImportJobs();
  const sampleJob = useImportJob(jobs.sample ?? null);
  const checkJob = useImportJob(jobs.check ?? null);
  const importJob = useImportJob(jobs.import ?? null);

  // A run of the file started before the page was opened again: followed here.
  useEffect(() => {
    const mine = (running.data ?? []).filter((job) => job.upload_id === current?.id);
    if (mine.some((job) => jobs[job.kind] !== job.id)) {
      setJobs((followed) => ({
        ...followed,
        ...Object.fromEntries(mine.map((job) => [job.kind, job.id])),
      }));
    }
  }, [running.data, current?.id, jobs]);

  const result = importJob.data?.status === "done" ? importJob.data.report : null;
  const imported = result !== null;
  // A run that ends changes the file's state: its sample, its check, or the file is gone.
  const statuses = [sampleJob.data?.status, checkJob.data?.status, importJob.data?.status].join();
  useEffect(() => {
    if (imported) {
      // The server forgot the loaded file with its cards: they are not asked for again.
      queryClient.setQueryData(["import-upload"], null);
      queryClient.removeQueries({ queryKey: ["import-sample"] });
      queryClient.removeQueries({ queryKey: ["import-cards"] });
      // Cards, cases, queues, lists and the history all may have changed.
      void queryClient.invalidateQueries({
        predicate: (q) => !["import-jobs", "import-upload"].includes(String(q.queryKey[0])),
      });
      return;
    }
    for (const key of ["import-upload", "import-sample", "import-cards"]) {
      void queryClient.invalidateQueries({ queryKey: [key] });
    }
  }, [statuses, imported, queryClient]);

  const starting = useMutation({
    mutationFn: (request: { kind: Kind; uploadId: string }) =>
      startImportJob(request.kind, request.uploadId, choices),
    onSuccess: (job) => {
      queryClient.setQueryData(["import-jobs", job.id], job);
      setJobs((followed) => ({ ...followed, [job.kind]: job.id }));
    },
  });
  const start = (kind: Kind) => current && starting.mutate({ kind, uploadId: current.id });

  const uploading = useMutation({
    mutationFn: (file: { name: string; content: string }) =>
      uploadImportFile(file.name, file.content),
    onSuccess: (next) => {
      queryClient.setQueryData(["import-upload"], next);
      setJobs({});
      setChoices({});
      // The sample cards come first: nothing goes into the CRM yet.
      starting.mutate({ kind: "sample", uploadId: next.id });
    },
  });
  const cancelling = useMutation({
    mutationFn: (id: string) => cancelImportUpload(id),
    onSuccess: () => {
      queryClient.setQueryData(["import-upload"], null);
      setJobs({});
      setChoices({});
      setInputKey((key) => key + 1);
    },
  });

  const chooseFile = async (picked: File | undefined) => {
    setFileError(null);
    if (!picked) return;
    if (picked.size > MAX_IMPORT_BYTES) {
      setFileError("file_too_large");
      return;
    }
    setJobs({});
    uploading.mutate({ name: picked.name.slice(-200), content: await readBase64(picked) });
  };

  const choose = (type: string, key: string, value: string) => {
    setChoices({ ...choices, [type]: { ...(choices[type] ?? {}), [key]: value } });
  };

  const isRunning = (job: ImportJob | undefined) => job?.status === "running";
  const importRunning =
    isRunning(importJob.data) || (starting.isPending && starting.variables?.kind === "import");
  const checkRunning = isRunning(checkJob.data);
  const cancel = current && !importRunning && (
    <button
      type="button"
      className="btn"
      disabled={cancelling.isPending}
      onClick={() => cancelling.mutate(current.id)}
    >
      {t("import.cancel")}
    </button>
  );

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
              disabled={importRunning}
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
        {uploading.isPending && <p className="muted">{t("import.sending")}</p>}
        {uploading.isError && <ErrorAlert error={uploading.error} />}
        {cancelling.isError && <ErrorAlert error={cancelling.error} />}
        {current && <Accepted upload={current} language={i18n.language} />}
      </section>

      {current && !current.checked && !checkRunning && (
        <section className="card import-step">
          <h2 className="h2">{t("import.stepSample")}</h2>
          <p className="muted import-note">{t("import.sampleNote")}</p>
          <JobState job={sampleJob.data} error={sampleJob.error} />
          {current.sampled && <Sample uploadId={current.id} />}
          {!isRunning(sampleJob.data) && (
            <div className="form-actions">
              <button
                type="button"
                className="btn btn-p"
                disabled={starting.isPending}
                onClick={() => start("check")}
              >
                {t(
                  current.sampled && current.clients > 0
                    ? "import.sampleRight"
                    : "import.checkWhole",
                )}
              </button>
              {!current.sampled && (
                <button
                  type="button"
                  className="btn"
                  disabled={starting.isPending}
                  onClick={() => start("sample")}
                >
                  {t("import.showSample")}
                </button>
              )}
              {cancel}
            </div>
          )}
          {starting.isError && <ErrorAlert error={starting.error} />}
        </section>
      )}

      {current && (current.checked || checkRunning) && (
        <section className="card import-step">
          <h2 className="h2">{t("import.stepCheck")}</h2>
          <JobState job={checkJob.data} error={checkJob.error} />
          {current.checked && current.report && (
            <>
              <p className="muted import-note">{t("import.checkNote")}</p>
              <ReportView report={current.report} dryRun />
              <UnknownValues report={current.report} choices={choices} onChange={choose} />
              <h3 className="h3">{t("import.cardsTitle")}</h3>
              <p className="muted small">{t("import.cardsNote")}</p>
              <CardList uploadId={current.id} />
              {!importRunning && (
                <>
                  <div className="form-actions import-confirm">
                    <button
                      type="button"
                      className="btn btn-p"
                      disabled={starting.isPending || current.report.imported === 0}
                      onClick={() => start("import")}
                    >
                      {t("import.confirm")}
                    </button>
                    <button
                      type="button"
                      className="btn"
                      disabled={starting.isPending}
                      onClick={() => start("check")}
                    >
                      {t("import.checkAgain")}
                    </button>
                    {cancel}
                  </div>
                  <p className="muted small">{t("import.confirmHint")}</p>
                </>
              )}
            </>
          )}
          {starting.isError && <ErrorAlert error={starting.error} />}
          <JobState job={importJob.data} error={importJob.error} />
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
                setJobs({});
                setInputKey(inputKey + 1);
              }}
            >
              {t("import.another")}
            </button>
          </div>
        </section>
      )}

      <History
        runs={runs.data}
        error={runs.isError ? runs.error : null}
        shown={[jobs.sample, jobs.check, jobs.import]}
      />
    </div>
  );
}

/** «Файл принят»: it waits on the server, nothing is in the CRM yet. */
function Accepted({ upload, language }: { upload: ImportUpload; language: string }) {
  const { t } = useTranslation();
  return (
    <p className="muted import-note">
      {t("import.fileAccepted", {
        name: upload.file_name,
        size: formatSize(upload.size, language),
      })}{" "}
      {t("import.fileHolds", { sheets: upload.sheets.length, clients: upload.clients })}
    </p>
  );
}

/** The cards of the file's first clients, as the CRM will have them. */
function Sample({ uploadId }: { uploadId: string }) {
  const { t, i18n } = useTranslation();
  const cards = useSampleCards(uploadId, promptLanguage(i18n.resolvedLanguage));
  if (cards.isError) return <ErrorAlert error={cards.error} />;
  if (!cards.data) return <p className="muted">{t("common.loading")}</p>;
  if (cards.data.length === 0) return <p className="muted">{t("import.sampleEmpty")}</p>;
  return (
    <div className="import-sample">
      {cards.data.map((card) => (
        <CardView key={card.n} card={card} />
      ))}
    </div>
  );
}

/** A running job's progress, or why it did not finish. A done job shows nothing here. */
function JobState({ job, error }: { job: ImportJob | undefined; error: unknown }) {
  const { t } = useTranslation();
  // A poll lost to a weak connection is asked again; only an answer of the server counts.
  if (error && !(job && job.status === "running")) return <ErrorAlert error={error} />;
  if (!job || job.status === "done") return null;
  if (job.status === "running") return <JobProgress job={job} />;
  return (
    <div className="alert alert-error" role="alert">
      {job.status === "stopped"
        ? t("import.stopped")
        : t(`errors.${job.error ?? "unknown"}`, { defaultValue: t("errors.unknown") })}
    </div>
  );
}

function JobProgress({ job }: { job: ImportJob }) {
  const { t } = useTranslation();
  const done = job.rows_done;
  const total = job.rows_total;
  const label =
    total === 0
      ? t("import.preparing")
      : t(job.kind === "import" ? "import.progressLoaded" : "import.progressChecked", {
          done,
          total,
        });
  return (
    <div className="import-progress" role="status">
      <span>{label}</span>
      <progress max={Math.max(total, 1)} value={total ? done : 0} aria-label={label} />
      <small className="muted">{t("import.progressHint")}</small>
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

function History({
  runs,
  error,
  shown,
}: {
  runs: ImportRunSummary[] | undefined;
  error: unknown;
  shown: (string | undefined)[];
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const report = useImportReport(open);
  // A job started before the page was opened again: shown here while it runs.
  const running = (useRunningImportJobs().data ?? []).filter((job) => !shown.includes(job.id));
  const busy = running.length > 0;
  const wasBusy = useRef(false);
  useEffect(() => {
    if (wasBusy.current && !busy) {
      void queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "import-jobs" });
    }
    wasBusy.current = busy;
  }, [busy, queryClient]);
  const when = (value: string) =>
    new Intl.DateTimeFormat(i18n.language, { dateStyle: "short", timeStyle: "short" }).format(
      new Date(value),
    );
  const runningText: Record<Kind, string> = {
    sample: "import.runningSample",
    check: "import.runningCheck",
    import: "import.runningImport",
  };
  return (
    <section className="card">
      <h2 className="h2">{t("import.history")}</h2>
      {running.map((job) => (
        <div key={job.id} className="import-running">
          <strong>{t(runningText[job.kind], { file: job.file_name })}</strong>
          <JobProgress job={job} />
        </div>
      ))}
      {error ? <ErrorAlert error={error} /> : null}
      {runs && runs.length === 0 && !busy && <p className="muted">{t("import.noRuns")}</p>}
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

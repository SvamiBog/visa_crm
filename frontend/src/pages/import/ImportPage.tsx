import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiGet, apiPost } from "../../api";
import { useCaseLists, useContactLists, type Entry } from "../../contacts";
import { readBase64 } from "../../files";
import { useCountries, useLocalName, type Country } from "../../reference";
import { ErrorAlert } from "../clients/ui";

// «Импорт таблиц» (backlog 1.14, 1.16): the specialist uploads a spreadsheet, maps its columns
// to fields, checks what would happen and imports it. The file stays in the browser and
// is sent again for the check and the import; the mapping is remembered by the server.

const KINDS = ["consultations", "cases", "queue", "attempts", "bookings"] as const;
type Kind = (typeof KINDS)[number];
const COUNTRY_KINDS: Kind[] = ["queue", "attempts"];
const CURRENCIES = ["BYN", "PLN", "USD", "EUR"];
const MAX_BYTES = 5 * 1024 * 1024;
// The order counts come in: people first, then what hangs on them.
const COUNT_ORDER = [
  "contacts",
  "consultations",
  "cases",
  "queue_entries",
  "attempts",
  "bookings",
  "finances",
  "payments",
  "vault_entries",
  "passwords",
  "notes",
  "channel",
  "partner",
  "purpose",
  "registrar",
];
const CREATABLE = ["channel", "partner", "purpose", "registrar"];
const SHOWN_ISSUES = 30;

type Column = { index: number; header: string; samples: string[]; field: string | null };
type FieldInfo = { key: string; type: string; required: boolean };
type VaultSystem = { id: string; name: string; kind: string };
type Options = {
  currency: string;
  submission_country_id: string | null;
  country_id: string | null;
  email_system_id: string | null;
  site_system_id: string | null;
};
type Choices = Record<string, Record<string, string>>;
type Preview = {
  sheets: string[];
  sheet: string;
  header_row: number;
  rows: number;
  columns: Column[];
  fields: FieldInfo[];
  options: Options;
  values: Choices;
  systems: VaultSystem[];
};
type Issue = { row: number; reason: string; column: string | null };
type Unknown = { key: string; value: string; rows: number };
type Report = {
  id: string | null;
  kind: Kind;
  file_name: string;
  sheet: string | null;
  rows: number;
  imported: number;
  skipped: Issue[];
  skipped_count: number;
  warnings: Issue[];
  warnings_count: number;
  created: Record<string, number>;
  updated: Record<string, number>;
  unknown: Record<string, Unknown[]>;
};
type RunSummary = {
  id: string;
  kind: Kind;
  file_name: string;
  sheet: string | null;
  rows: number;
  imported: number;
  skipped_count: number;
  warnings_count: number;
  created_by: string | null;
  created_at: string;
};
type Upload = { name: string; content: string };

export function ImportPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<Kind>("consultations");
  const [upload, setUpload] = useState<Upload | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [sheet, setSheet] = useState<string | null>(null);
  const [headerRow, setHeaderRow] = useState<number | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [columns, setColumns] = useState<Record<number, string>>({});
  const [options, setOptions] = useState<Options | null>(null);
  const [choices, setChoices] = useState<Choices>({});
  const [checked, setChecked] = useState<Report | null>(null);
  const [result, setResult] = useState<Report | null>(null);
  // A new key clears the file input for the next table.
  const [inputKey, setInputKey] = useState(0);

  const runs = useQuery({ queryKey: ["imports"], queryFn: () => apiGet<RunSummary[]>("/imports") });

  const file = (extra: { sheet?: string | null; headerRow?: number | null } = {}) => ({
    kind,
    file_name: upload?.name ?? "",
    content: upload?.content ?? "",
    sheet: extra.sheet !== undefined ? extra.sheet : sheet,
    header_row: extra.headerRow !== undefined ? extra.headerRow : headerRow,
  });

  const previewing = useMutation({
    mutationFn: (body: ReturnType<typeof file>) => apiPost<Preview>("/imports/preview", body),
    onSuccess: (data) => {
      setPreview(data);
      setSheet(data.sheet || null);
      setHeaderRow(data.header_row);
      setColumns(
        Object.fromEntries(
          data.columns.filter((c) => c.field).map((c) => [c.index, c.field as string]),
        ),
      );
      setOptions(data.options);
      setChoices(data.values);
      setChecked(null);
      setResult(null);
    },
  });

  const runBody = () => ({
    ...file(),
    columns,
    options: Object.fromEntries(Object.entries(options ?? {}).filter(([, v]) => v !== null)),
    values: choices,
  });
  const checking = useMutation({
    mutationFn: () => apiPost<Report>("/imports/check", runBody()),
    onSuccess: setChecked,
  });
  const importing = useMutation({
    mutationFn: () => apiPost<Report>("/imports", runBody()),
    onSuccess: (report) => {
      setResult(report);
      setChecked(null);
      // Cards, cases, queues and lists all may have changed.
      void queryClient.invalidateQueries();
    },
  });

  const reset = (next: Partial<{ keepFile: boolean }> = {}) => {
    setPreview(null);
    setChecked(null);
    setResult(null);
    if (!next.keepFile) {
      setUpload(null);
      setSheet(null);
      setHeaderRow(null);
    }
  };

  const chooseKind = (next: Kind) => {
    setKind(next);
    setPreview(null);
    setChecked(null);
    setResult(null);
    if (upload) {
      previewing.mutate({ ...file(), kind: next });
    }
  };

  const chooseFile = async (picked: File | undefined) => {
    reset();
    setFileError(null);
    if (!picked) return;
    if (picked.size > MAX_BYTES) {
      setFileError("file_too_large");
      return;
    }
    const content = await readBase64(picked);
    const next = { name: picked.name.slice(-200), content };
    setUpload(next);
    previewing.mutate({
      kind,
      file_name: next.name,
      content,
      sheet: null,
      header_row: null,
    });
  };

  // Any change to the mapping or the options makes the last check stale.
  const changeMapping = (update: () => void) => {
    update();
    setChecked(null);
  };

  const fields = preview?.fields ?? [];
  const used = new Set(Object.values(columns));
  const missing = fields.filter((f) => f.required && !used.has(f.key));
  const needsCountry = COUNTRY_KINDS.includes(kind) && !options?.country_id;
  const vaultSlots = (["email", "site"] as const).filter(
    (slot) => used.has(`${slot}_login`) || used.has(`${slot}_password`),
  );
  const needsSystem = vaultSlots.some((slot) => !options?.[`${slot}_system_id`]);
  const ready = preview && missing.length === 0 && !needsCountry && !needsSystem;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("import.title")}</h1>
          <div className="muted">{t("import.subtitle")}</div>
        </div>
      </header>

      <section className="card import-step">
        <h2 className="h2">{t("import.stepFile")}</h2>
        <p className="muted import-note">{t("import.order")}</p>
        <div className="theme-picker import-kinds" role="radiogroup" aria-label={t("import.kindLabel")}>
          {KINDS.map((k) => (
            <label key={k} className={`theme-option${k === kind ? " selected" : ""}`}>
              <input
                type="radio"
                name="import-kind"
                checked={k === kind}
                onChange={() => chooseKind(k)}
              />
              <span className="theme-name">{t(`import.kind.${k}`)}</span>
              <span className="muted small">{t(`import.kindHint.${k}`)}</span>
            </label>
          ))}
        </div>
        <div className="form-grid import-file">
          <label className="field">
            <span>{t("import.file")}</span>
            <input
              key={inputKey}
              type="file"
              accept=".xlsx,.csv,.tsv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv,text/tab-separated-values"
              onChange={(e) => void chooseFile(e.target.files?.[0])}
            />
            <small className="muted">{t("import.fileHint")}</small>
          </label>
          {preview && preview.sheets.length > 1 && (
            <label className="field">
              <span>{t("import.sheet")}</span>
              <select
                value={preview.sheet}
                onChange={(e) => previewing.mutate(file({ sheet: e.target.value, headerRow: null }))}
              >
                {preview.sheets.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {preview && (
            <label className="field">
              <span>{t("import.headerRow")}</span>
              <input
                type="number"
                min={1}
                max={20}
                value={preview.header_row}
                onChange={(e) => {
                  const value = Number(e.target.value);
                  if (value >= 1 && value <= 20) previewing.mutate(file({ headerRow: value }));
                }}
              />
              <small className="muted">{t("import.headerRowHint")}</small>
            </label>
          )}
        </div>
        {fileError && (
          <div className="alert alert-error" role="alert">
            {t(`errors.${fileError}`)}
          </div>
        )}
        {previewing.isPending && <p className="muted">{t("import.reading")}</p>}
        {previewing.isError && <ErrorAlert error={previewing.error} />}
      </section>

      {preview && options && !result && (
        <section className="card import-step">
          <h2 className="h2">{t("import.stepColumns")}</h2>
          <p className="muted import-note">
            {t("import.found", { rows: preview.rows, header: preview.header_row })}{" "}
            {t("import.rules")}
          </p>
          <ImportOptions
            kind={kind}
            preview={preview}
            options={options}
            vaultSlots={vaultSlots}
            onChange={(next) => changeMapping(() => setOptions(next))}
          />
          <div className="table-wrap">
            <table className="tbl import-columns">
              <thead>
                <tr>
                  <th>{t("import.colHeader")}</th>
                  <th>{t("import.colSamples")}</th>
                  <th>{t("import.colField")}</th>
                </tr>
              </thead>
              <tbody>
                {preview.columns.map((column) => (
                  <tr key={column.index} className={columns[column.index] ? undefined : "row-off"}>
                    <td>
                      {column.header || (
                        <span className="muted">
                          {t("import.noHeader", { number: column.index + 1 })}
                        </span>
                      )}
                    </td>
                    <td className="muted small import-samples">
                      {column.samples.length ? column.samples.join(" · ") : "—"}
                    </td>
                    <td>
                      <select
                        aria-label={t("import.fieldFor", {
                          column: column.header || column.index + 1,
                        })}
                        value={columns[column.index] ?? ""}
                        onChange={(e) =>
                          changeMapping(() => {
                            const next = { ...columns };
                            if (e.target.value) next[column.index] = e.target.value;
                            else delete next[column.index];
                            setColumns(next);
                          })
                        }
                      >
                        <option value="">{t("import.skipColumn")}</option>
                        {fields.map((f) => (
                          <option
                            key={f.key}
                            value={f.key}
                            disabled={used.has(f.key) && columns[column.index] !== f.key}
                          >
                            {t(`import.field.${f.key}`)}
                            {f.required ? " *" : ""}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {missing.length > 0 && (
            <div className="alert alert-warn import-note" role="status">
              {t("import.missing", {
                fields: missing.map((f) => t(`import.field.${f.key}`)).join(", "),
              })}
            </div>
          )}
          {needsCountry && (
            <div className="alert alert-warn import-note" role="status">
              {t("errors.country_required")}
            </div>
          )}
          {needsSystem && (
            <div className="alert alert-warn import-note" role="status">
              {t("errors.system_required")}
            </div>
          )}
          <div className="form-actions">
            <button
              type="button"
              className="btn btn-p"
              disabled={!ready || checking.isPending}
              onClick={() => checking.mutate()}
            >
              {checking.isPending ? t("import.checking") : t("import.check")}
            </button>
          </div>
          {checking.isError && <ErrorAlert error={checking.error} />}
        </section>
      )}

      {checked && !result && (
        <section className="card import-step">
          <h2 className="h2">{t("import.stepCheck")}</h2>
          <p className="muted import-note">{t("import.checkNote")}</p>
          <ReportView report={checked} dryRun />
          <UnknownValues
            report={checked}
            choices={choices}
            onChange={(type, key, value) =>
              setChoices({ ...choices, [type]: { ...(choices[type] ?? {}), [key]: value } })
            }
          />
          <div className="form-actions">
            <button
              type="button"
              className="btn btn-p"
              disabled={importing.isPending || checked.imported === 0}
              onClick={() => importing.mutate()}
            >
              {importing.isPending
                ? t("import.importing")
                : t("import.run", { count: checked.imported })}
            </button>
            <button
              type="button"
              className="btn"
              disabled={checking.isPending}
              onClick={() => checking.mutate()}
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
                reset();
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

function ImportOptions({
  kind,
  preview,
  options,
  vaultSlots,
  onChange,
}: {
  kind: Kind;
  preview: Preview;
  options: Options;
  vaultSlots: ("email" | "site")[];
  onChange: (next: Options) => void;
}) {
  const { t } = useTranslation();
  const countries = useCountries();
  const name = useLocalName();
  const sorted = useMemo(
    () =>
      [...(countries.data ?? [])]
        .filter((c) => c.is_active)
        .sort((a, b) => name.country(a).localeCompare(name.country(b))),
    [countries.data, name],
  );
  return (
    <div className="form-grid import-options">
      {COUNTRY_KINDS.includes(kind) && (
        <CountrySelect
          label={t("import.country")}
          countries={sorted.filter((c) => c.id !== options.submission_country_id)}
          value={options.country_id}
          onChange={(country_id) => onChange({ ...options, country_id })}
          required
        />
      )}
      {kind !== "bookings" && (
        <CountrySelect
          label={t("import.submissionCountry")}
          countries={sorted}
          value={options.submission_country_id}
          onChange={(submission_country_id) => onChange({ ...options, submission_country_id })}
        />
      )}
      <label className="field">
        <span>{t("import.currency")}</span>
        <select
          value={options.currency}
          onChange={(e) => onChange({ ...options, currency: e.target.value })}
        >
          {CURRENCIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <small className="muted">{t("import.currencyHint")}</small>
      </label>
      {vaultSlots.map((slot) => (
        <label key={slot} className="field">
          <span>{t(`import.system.${slot}`)}</span>
          <select
            value={options[`${slot}_system_id`] ?? ""}
            onChange={(e) =>
              onChange({ ...options, [`${slot}_system_id`]: e.target.value || null })
            }
          >
            <option value="">{t("import.choose")}</option>
            {preview.systems.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
      ))}
    </div>
  );
}

function CountrySelect({
  label,
  countries,
  value,
  onChange,
  required = false,
}: {
  label: string;
  countries: Country[];
  value: string | null;
  onChange: (id: string | null) => void;
  required?: boolean;
}) {
  const { t } = useTranslation();
  const name = useLocalName();
  return (
    <label className="field">
      <span>
        {label}
        {required ? " *" : ""}
      </span>
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">{t("import.choose")}</option>
        {countries.map((c) => (
          <option key={c.id} value={c.id}>
            {name.country(c)}
          </option>
        ))}
      </select>
    </label>
  );
}

function ReportView({ report, dryRun = false }: { report: Report; dryRun?: boolean }) {
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
      <Issues
        title={t("import.skippedRows")}
        issues={report.skipped}
        total={report.skipped_count}
      />
      <Issues title={t("import.warningRows")} issues={report.warnings} total={report.warnings_count} />
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

function Issues({ title, issues, total }: { title: string; issues: Issue[]; total: number }) {
  const { t } = useTranslation();
  const [all, setAll] = useState(false);
  if (issues.length === 0) return null;
  const shown = all ? issues : issues.slice(0, SHOWN_ISSUES);
  return (
    <div className="import-issues">
      <h3 className="h3">{title}</h3>
      <ul className="plain-list import-issue-list">
        {shown.map((issue, i) => (
          <li key={`${issue.row}-${issue.column ?? ""}-${i}`}>
            <span className="mono">{t("import.row", { row: issue.row })}</span>
            {issue.column ? ` · «${issue.column}»` : ""} — <IssueText reason={issue.reason} />
          </li>
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

function IssueText({ reason }: { reason: string }): ReactNode {
  const { t } = useTranslation();
  return t(`import.reason.${reason}`, {
    defaultValue: reason.startsWith("bad_") ? t("import.reason.bad") : reason,
  });
}

function UnknownValues({
  report,
  choices,
  onChange,
}: {
  report: Report;
  choices: Choices;
  onChange: (type: string, key: string, value: string) => void;
}) {
  const { t } = useTranslation();
  const { channels, partners } = useContactLists();
  const { purposes, registrars } = useCaseLists();
  const countries = useCountries();
  const name = useLocalName();
  const lists: Record<string, Entry[]> = {
    channel: channels,
    partner: partners,
    purpose: purposes,
    registrar: registrars,
    country: (countries.data ?? [])
      .map((c) => ({ id: c.id, name: name.country(c), is_active: c.is_active }))
      .sort((a, b) => a.name.localeCompare(b.name)),
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
              report.unknown[type].map((item) => {
                const creatable = CREATABLE.includes(type);
                const value = choices[type]?.[item.key] ?? (creatable ? "new" : "skip");
                // Two spellings of one registrar: this one goes «as» another new value.
                const others = creatable
                  ? report.unknown[type].filter(
                      (other) =>
                        other.key !== item.key &&
                        !(choices[type]?.[other.key] ?? "new").startsWith("as:"),
                    )
                  : [];
                return (
                  <tr key={`${type}-${item.key}`}>
                    <td className="muted">{t(`import.listName.${type}`)}</td>
                    <td>{item.value}</td>
                    <td className="money-cell">{item.rows}</td>
                    <td>
                      {type === "city" ? (
                        <span className="muted small">{t("import.cityUnknown")}</span>
                      ) : (
                        <select
                          aria-label={t("import.actionFor", { value: item.value })}
                          value={value}
                          onChange={(e) => onChange(type, item.key, e.target.value)}
                        >
                          {creatable && <option value="new">{t("import.createNew")}</option>}
                          <option value="skip">{t("import.leaveEmpty")}</option>
                          {type === "channel" && (
                            <option value="partner">{t("import.asPartner")}</option>
                          )}
                          {others.map((other) => (
                            <option key={other.key} value={`as:${other.value}`}>
                              {t("import.asValue", { value: other.value })}
                            </option>
                          ))}
                          {(lists[type] ?? [])
                            .filter((entry) => entry.is_active)
                            .map((entry) => (
                              <option key={entry.id} value={entry.id}>
                                {entry.name}
                              </option>
                            ))}
                        </select>
                      )}
                    </td>
                  </tr>
                );
              }),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function History({ runs, error }: { runs: RunSummary[] | undefined; error: unknown }) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState<string | null>(null);
  const report = useQuery({
    queryKey: ["imports", open],
    queryFn: () => apiGet<Report>(`/imports/${open}`),
    enabled: open !== null,
  });
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
                <th>{t("import.table")}</th>
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
                    {run.sheet ? ` · ${run.sheet}` : ""}
                    <div className="muted small">{t(`import.kind.${run.kind}`)}</div>
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

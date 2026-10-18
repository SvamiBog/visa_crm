import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, apiDelete, apiDownload, apiGet, apiPatch, apiPost, apiPut } from "../../api";
import { useMe } from "../../auth";
import { formatSize, readBase64 } from "../../files";
import {
  DOCX_TYPES,
  MAX_TEMPLATE_BYTES,
  TEMPLATES_KEY,
  codeText,
  useTemplateCodes,
  useTemplates,
  type DocumentTemplate,
  type TemplateCode,
} from "../../templates";
import { ErrorAlert } from "../clients/ui";

const GROUPS = ["client", "passport", "case", "money", "other"] as const;

/** The specialist's document templates: her Word files with codes, the instruction and
 * every code. Templates are given to clients in the client's card. */
export function TemplatesPage() {
  const { t } = useTranslation();
  const me = useMe();
  const canEdit = me.data?.role === "specialist";
  const templates = useTemplates();
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<{ modules: string[]; file_storage: boolean }>("/workspace"),
  });
  const fileStorage = workspace.data?.file_storage === true;
  const [adding, setAdding] = useState(false);

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("templates.title")}</h1>
          <div className="muted">{t("templates.subtitle")}</div>
        </div>
        {canEdit && fileStorage && !adding && (
          <div className="actions">
            <button type="button" className="btn btn-p" onClick={() => setAdding(true)}>
              {t("templates.upload")}
            </button>
          </div>
        )}
      </header>
      {workspace.data && !fileStorage && <p className="muted">{t("templates.storageOff")}</p>}
      {adding && <UploadForm onDone={() => setAdding(false)} />}

      <section className="card">
        <h2 className="h2">{t("templates.mine")}</h2>
        {templates.isPending && <p className="muted">{t("common.loading")}</p>}
        {templates.isError && <ErrorAlert error={templates.error} />}
        {templates.data?.length === 0 && (
          <p className="muted small">{t(canEdit ? "templates.empty" : "templates.emptyEmployee")}</p>
        )}
        {templates.data && templates.data.length > 0 && (
          <ul className="booking-list templates">
            {templates.data.map((template) => (
              <TemplateItem key={template.id} template={template} canEdit={canEdit} />
            ))}
          </ul>
        )}
      </section>

      <Instruction />
      <CodeTable />
    </div>
  );
}

/** A file from the computer: a Word file of up to 5 MB, checked again by the server. */
function useChosenFile() {
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<unknown>(null);
  // A new input after each pick: choosing the same file again fires onChange.
  const [inputKey, setInputKey] = useState(0);
  const choose = (picked: File | undefined) => {
    setInputKey((key) => key + 1);
    setError(null);
    if (picked && picked.size > MAX_TEMPLATE_BYTES) {
      setError(new ApiError(413, "template_too_large"));
      setFile(null);
      return;
    }
    setFile(picked ?? null);
  };
  return { file, error, setError, inputKey, choose };
}

function UploadForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const chosen = useChosenFile();
  const [name, setName] = useState("");
  const [fileName, setFileName] = useState("");
  const save = useMutation({
    mutationFn: async (file: File) =>
      apiPost<DocumentTemplate>("/templates", {
        name: name.trim() || null,
        file_name: fileName.trim() || null,
        source_name: file.name.slice(-255),
        content: await readBase64(file),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: TEMPLATES_KEY });
      onDone();
    },
    onError: chosen.setError,
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (chosen.file) save.mutate(chosen.file);
  };

  return (
    <form className="card form-card" onSubmit={submit}>
      <h2 className="h2">{t("templates.uploadTitle")}</h2>
      <div className="field">
        <span>{t("templates.file")}</span>
        <label className="btn btn-sm file-button">
          {chosen.file ? chosen.file.name : t("templates.chooseFile")}
          <input
            key={chosen.inputKey}
            type="file"
            className="visually-hidden"
            accept={DOCX_TYPES}
            onChange={(e) => chosen.choose(e.target.files?.[0])}
          />
        </label>
        <small className="muted">{t("templates.fileHint")}</small>
      </div>
      <label className="field">
        <span>{t("templates.name")}</span>
        <input
          maxLength={200}
          value={name}
          placeholder={chosen.file?.name.replace(/\.docx$/i, "")}
          onChange={(e) => setName(e.target.value)}
        />
        <small className="muted">{t("templates.nameHint")}</small>
      </label>
      <FileNameField value={fileName} onChange={setFileName} />
      {chosen.error !== null && <ErrorAlert error={chosen.error} />}
      <div className="form-actions">
        <button type="submit" className="btn btn-sm btn-p" disabled={!chosen.file || save.isPending}>
          {save.isPending ? t("templates.uploading") : t("templates.save")}
        </button>
        <button type="button" className="btn btn-sm" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

function FileNameField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation();
  return (
    <label className="field">
      <span>{t("templates.fileName")}</span>
      <input
        maxLength={200}
        value={value}
        placeholder={t("templates.fileNamePlaceholder")}
        onChange={(e) => onChange(e.target.value)}
      />
      <small className="muted">{t("templates.fileNameHint")}</small>
    </label>
  );
}

function TemplateItem({ template, canEdit }: { template: DocumentTemplate; canEdit: boolean }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const codes = useTemplateCodes();
  const byKey = useMemo(() => new Map((codes.data ?? []).map((c) => [c.key, c])), [codes.data]);
  const [editing, setEditing] = useState(false);
  const chosen = useChosenFile();
  const url = `/templates/${template.id}`;
  const refresh = () => queryClient.invalidateQueries({ queryKey: TEMPLATES_KEY });

  const toggle = useMutation({
    mutationFn: () => apiPatch<DocumentTemplate>(url, { is_active: !template.is_active }),
    onSuccess: refresh,
    onError: chosen.setError,
  });
  const replace = useMutation({
    mutationFn: async (file: File) =>
      apiPut<DocumentTemplate>(`${url}/file`, {
        source_name: file.name.slice(-255),
        content: await readBase64(file),
      }),
    onSuccess: refresh,
    onError: chosen.setError,
  });
  const download = useMutation({
    mutationFn: () => apiDownload(`${url}/file`, template.source_name),
    onError: chosen.setError,
  });
  const remove = useMutation({
    mutationFn: () => apiDelete<void>(url),
    onSuccess: refresh,
    onError: chosen.setError,
  });
  const confirmDelete = () => {
    const question = template.clients
      ? t("templates.confirmDeleteUsed", { name: template.name, count: template.clients })
      : t("templates.confirmDelete", { name: template.name });
    if (window.confirm(question)) remove.mutate();
  };

  if (editing) {
    return (
      <li className="booking-item">
        <EditForm template={template} onDone={() => setEditing(false)} />
      </li>
    );
  }
  return (
    <li className={template.is_active ? "booking-item" : "booking-item row-off"}>
      <div className="booking-line">
        <strong>{template.name}</strong>
        <span className="muted small">
          {template.source_name} · {formatSize(template.size_bytes, i18n.language)}
          {template.clients > 0 && ` · ${t("templates.clients", { count: template.clients })}`}
        </span>
      </div>
      <p className="small">
        <span className="muted">{t("templates.fileNameShort")}:</span>{" "}
        {template.file_name ?? t("templates.fileNameDefault", { name: template.name })}
      </p>
      <div className="chips">
        {template.codes.length === 0 && (
          <span className="chip st-gray">{t("templates.noCodes")}</span>
        )}
        {template.codes.map((key) => (
          <span key={key} className="chip st-blue" title={t(`templates.code.${key}`)}>
            {codeText(byKey.get(key), i18n.language)}
          </span>
        ))}
        {!template.is_active && <span className="chip st-gray">{t("reference.off")}</span>}
      </div>
      {template.unknown_codes.length > 0 && (
        <p className="small templates-unknown">
          <span className="chip st-orange">{t("templates.unknown")}</span>{" "}
          {template.unknown_codes.map((code) => `{{${code}}}`).join(", ")}.{" "}
          <span className="muted">{t("templates.unknownHint")}</span>
        </p>
      )}
      <div className="note-actions">
        <button
          type="button"
          className="link-button"
          disabled={download.isPending}
          onClick={() => download.mutate()}
        >
          {t("templates.downloadBlank")}
        </button>
        {canEdit && (
          <>
            <button type="button" className="link-button" onClick={() => setEditing(true)}>
              {t("reference.edit")}
            </button>
            <label className="link-button file-link">
              {replace.isPending ? t("templates.uploading") : t("templates.replace")}
              <input
                key={chosen.inputKey}
                type="file"
                className="visually-hidden"
                accept={DOCX_TYPES}
                disabled={replace.isPending}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  chosen.choose(file);
                  if (file && file.size <= MAX_TEMPLATE_BYTES) replace.mutate(file);
                }}
              />
            </label>
            <button
              type="button"
              className="link-button"
              disabled={toggle.isPending}
              onClick={() => toggle.mutate()}
            >
              {t(template.is_active ? "reference.switchOff" : "reference.switchOn")}
            </button>
            <button
              type="button"
              className="link-button"
              disabled={remove.isPending}
              onClick={confirmDelete}
            >
              {t("templates.delete")}
            </button>
          </>
        )}
      </div>
      {chosen.error !== null && <ErrorAlert error={chosen.error} />}
    </li>
  );
}

function EditForm({ template, onDone }: { template: DocumentTemplate; onDone: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [name, setName] = useState(template.name);
  const [fileName, setFileName] = useState(template.file_name ?? "");
  const save = useMutation({
    mutationFn: () =>
      apiPatch<DocumentTemplate>(`/templates/${template.id}`, {
        name: name.trim(),
        file_name: fileName.trim() || null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: TEMPLATES_KEY });
      onDone();
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  return (
    <form className="form-card booking-form" onSubmit={submit}>
      <label className="field">
        <span>{t("templates.name")}</span>
        <input
          required
          autoFocus
          maxLength={200}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <FileNameField value={fileName} onChange={setFileName} />
      {save.isError && <ErrorAlert error={save.error} />}
      <div className="form-actions">
        <button type="submit" className="btn btn-sm btn-p" disabled={save.isPending || !name.trim()}>
          {t("common.save")}
        </button>
        <button type="button" className="btn btn-sm" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

/** How to make a template out of one's own document, step by step. */
function Instruction() {
  const { t } = useTranslation();
  const steps = ["write", "codes", "save", "upload", "give", "download"] as const;
  const rules = ["anyCase", "language", "grammar", "format", "where", "empty", "unknown"] as const;
  return (
    <section className="card">
      <h2 className="h2">{t("templates.howTo.title")}</h2>
      <ol className="steps">
        {steps.map((step) => (
          <li key={step}>{t(`templates.howTo.${step}`)}</li>
        ))}
      </ol>
      <h3 className="h3">{t("templates.rules.title")}</h3>
      <ul className="steps">
        {rules.map((rule) => (
          <li key={rule}>{t(`templates.rules.${rule}`)}</li>
        ))}
      </ul>
    </section>
  );
}

/** Every code, in Russian and in English, copied with a tap. */
function CodeTable() {
  const { t } = useTranslation();
  const codes = useTemplateCodes();
  const [copied, setCopied] = useState("");
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(text);
    } catch {
      setCopied("");
    }
  };
  const button = (text: string) => (
    <button type="button" className="link-button mono" onClick={() => void copy(text)}>
      {text}
      {copied === text && <span className="muted small"> · {t("templates.copied")}</span>}
    </button>
  );
  const rows = (group: string) => (codes.data ?? []).filter((c: TemplateCode) => c.group === group);

  return (
    <section className="card">
      <h2 className="h2">{t("templates.codesTitle")}</h2>
      <p className="muted small">{t("templates.codesHint")}</p>
      {codes.isError && <ErrorAlert error={codes.error} />}
      <div className="table-wrap">
        <table className="tbl template-codes">
          <thead>
            <tr>
              <th>{t("templates.codeMeaning")}</th>
              <th>{t("templates.codeRu")}</th>
              <th>{t("templates.codeEn")}</th>
            </tr>
          </thead>
          {GROUPS.map((group) => (
            <tbody key={group}>
              <tr>
                <th colSpan={3}>{t(`templates.group.${group}`)}</th>
              </tr>
              {rows(group).map((code) => (
                <tr key={code.key}>
                  <td>{t(`templates.code.${code.key}`)}</td>
                  <td data-label={t("templates.codeRu")}>{button(`{{${code.ru}}}`)}</td>
                  <td data-label={t("templates.codeEn")}>{button(`{{${code.en}}}`)}</td>
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </div>
    </section>
  );
}

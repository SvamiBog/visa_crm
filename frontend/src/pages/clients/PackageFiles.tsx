import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, apiGet } from "../../api";
import { useMe } from "../../auth";
import { useConfirm } from "../../components/ConfirmDialog";
import { formatSize, readBase64 } from "../../files";
import type { PackageFile, PackageItem } from "../../package";
import {
  MAX_PORTAL_FILE_BYTES,
  PORTAL_FILE_TYPES,
  RETURN_REASONS,
  downloadFile,
  useAcceptFile,
  useAddFile,
  useDeleteFile,
  useReturnFile,
  type ReturnReason,
} from "../../portal";
import { ErrorAlert } from "./ui";

// Files at an item of the case's checklist (backlog 2.9): the client's uploads from the
// portal wait for «Принять» (the item becomes «есть» with the document's date) or
// «Вернуть» with a reason the client sees. The specialist's own upload — a document the
// client sent in a messenger or brought — is accepted at once.

const FILE_CHIP: Record<PackageFile["status"], string> = {
  pending: "chip st-orange",
  accepted: "chip st-green",
  returned: "chip st-red",
};

export function ItemFiles({
  caseId,
  item,
  documentDate,
}: {
  caseId: string;
  item: PackageItem;
  documentDate: string;
}) {
  const { t, i18n } = useTranslation();
  const add = useAddFile(caseId, item.item_id);
  // Without a file storage (a stand without S3) nothing can be added, as on the card.
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<{ file_storage: boolean }>("/workspace"),
  });
  const fileStorage = workspace.data?.file_storage === true;
  const [inputKey, setInputKey] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const added = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium" });

  const choose = async (file: File | undefined) => {
    setInputKey((key) => key + 1);
    setError(null);
    if (!file) return;
    if (file.size > MAX_PORTAL_FILE_BYTES) {
      setError(new ApiError(413, "document_too_large_20"));
      return;
    }
    add.mutate(
      {
        file_name: file.name.slice(-255),
        content: await readBase64(file),
        document_date: documentDate || null,
      },
      { onError: setError },
    );
  };

  return (
    <div className="package-files">
      {item.files.length > 0 && (
        <ul className="plain-list">
          {item.files.map((file) => (
            <FileRow key={file.id} caseId={caseId} file={file} added={added} />
          ))}
        </ul>
      )}
      {fileStorage && (
        <label className="btn btn-sm file-button">
          {add.isPending ? t("contacts.uploadingDocument") : t("portal.addFile")}
          <input
            key={inputKey}
            type="file"
            className="visually-hidden"
            accept={PORTAL_FILE_TYPES}
            disabled={add.isPending}
            onChange={(e) => void choose(e.target.files?.[0])}
          />
        </label>
      )}
      {error !== null && <ErrorAlert error={error} />}
    </div>
  );
}

function FileRow({
  caseId,
  file,
  added,
}: {
  caseId: string;
  file: PackageFile;
  added: Intl.DateTimeFormat;
}) {
  const { t, i18n } = useTranslation();
  const me = useMe();
  const confirm = useConfirm();
  const accept = useAcceptFile(caseId);
  const giveBack = useReturnFile(caseId);
  const remove = useDeleteFile(caseId);
  const [error, setError] = useState<unknown>(null);
  const [mode, setMode] = useState<"accept" | "return" | null>(null);
  const [date, setDate] = useState("");
  const [reason, setReason] = useState<ReturnReason>("unreadable");
  const [note, setNote] = useState("");
  const busy = accept.isPending || giveBack.isPending || remove.isPending;
  const mayDelete = me.data?.role === "specialist" || file.source === "staff";

  const download = async () => {
    setError(null);
    try {
      await downloadFile(caseId, file);
    } catch (e) {
      setError(e);
    }
  };

  return (
    <li className="package-file">
      <div className="package-line">
        <span>
          <button
            type="button"
            className="link-button"
            title={t("contacts.downloadDocument")}
            onClick={() => void download()}
          >
            {file.file_name}
          </button>
          <span className="muted small">
            {" · "}
            {formatSize(file.size_bytes, i18n.language)} · {added.format(new Date(file.created_at))}
            {" · "}
            {file.source === "client" ? t("portal.fromClient") : (file.uploaded_by_email ?? "")}
          </span>
        </span>
        <span className={FILE_CHIP[file.status]}>{t(`portal.fileStatus.${file.status}`)}</span>
      </div>
      {file.status === "returned" && file.return_reason && (
        <p className="muted small">
          {file.return_reason === "other"
            ? file.return_note
            : t(`portal.reason.${file.return_reason}`)}
        </p>
      )}
      {file.status === "pending" && mode === null && (
        <div className="form-actions">
          <button type="button" className="btn btn-sm btn-p" onClick={() => setMode("accept")}>
            {t("portal.accept")}
          </button>
          <button type="button" className="btn btn-sm" onClick={() => setMode("return")}>
            {t("portal.return")}
          </button>
        </div>
      )}
      {mode === "accept" && (
        <form
          className="package-review"
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            accept.mutate(
              { file, document_date: date || null },
              { onSuccess: () => setMode(null), onError: setError },
            );
          }}
        >
          <label className="field">
            <span>{t("package.documentDate")}</span>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <div className="form-actions">
            <button type="submit" className="btn btn-sm btn-p" disabled={busy}>
              {t("portal.accept")}
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setMode(null)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      )}
      {mode === "return" && (
        <form
          className="package-review"
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            giveBack.mutate(
              { file, reason, note: note.trim() || null },
              { onSuccess: () => setMode(null), onError: setError },
            );
          }}
        >
          <label className="field">
            <span>{t("portal.returnReason")}</span>
            <select value={reason} onChange={(e) => setReason(e.target.value as ReturnReason)}>
              {RETURN_REASONS.map((r) => (
                <option key={r} value={r}>
                  {t(`portal.reason.${r}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{reason === "other" ? t("portal.returnNote") : t("portal.returnNoteOptional")}</span>
            <input
              value={note}
              maxLength={300}
              required={reason === "other"}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          <div className="form-actions">
            <button type="submit" className="btn btn-sm btn-p" disabled={busy}>
              {t("portal.returnSend")}
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setMode(null)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      )}
      {mayDelete && mode === null && (
        <button
          type="button"
          className="link-button small"
          disabled={busy}
          onClick={async () => {
            setError(null);
            const yes = await confirm({
              message: t("contacts.confirmDeleteDocument", { name: file.file_name }),
              action: t("confirm.delete"),
            });
            if (yes) remove.mutate(file, { onError: setError });
          }}
        >
          {t("contacts.deleteDocument")}
        </button>
      )}
      {error !== null && <ErrorAlert error={error} />}
    </li>
  );
}

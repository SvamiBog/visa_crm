import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import {
  ApiError,
  apiDelete,
  apiDownload,
  apiGet,
  apiPatch,
  apiPost,
  errorCode,
} from "../../api";
import { useMe } from "../../auth";
import {
  contactKey,
  formatDate,
  formatPhone,
  telegramUrl,
  useContactLists,
  type Consultation,
  type ContactCard,
  type ContactDocument,
  type ContactListItem,
  type DocumentKind,
  type Note,
} from "../../contacts";
import { formatSize, readBase64 } from "../../files";
import { byId } from "../../reference";
import { AttentionList } from "../cabinet/Attention";
import { ContactVault } from "../vault/Vault";
import { Cases } from "./Cases";
import { ContactForm } from "./ContactForm";
import { Consultations, Hints, QuestionnaireCard } from "./Consultations";

function ErrorAlert({ error }: { error: unknown }) {
  const { t } = useTranslation();
  return (
    <div className="alert alert-error" role="alert">
      {t(`errors.${errorCode(error)}`, { defaultValue: t("errors.unknown") })}
    </div>
  );
}

/** The client's card: contact details, questionnaire and its checks, cases,
 * consultations, paperwork, family and links, the notes timeline. */
export function ContactPage() {
  const { id = "" } = useParams();
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const card = useQuery({
    queryKey: contactKey(id),
    queryFn: () => apiGet<ContactCard>(`/contacts/${id}`),
  });
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<{ modules: string[]; file_storage: boolean }>("/workspace"),
  });
  const [editing, setEditing] = useState(false);
  // «Создать кейс» from a consultation, or a new case without one.
  const [creatingCase, setCreatingCase] = useState<Consultation | "blank" | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["contacts"] });

  if (card.isPending) return <p className="muted">{t("common.loading")}</p>;
  if (card.isError) {
    return (
      <div className="page">
        <ErrorAlert error={card.error} />
        <Link to="/app/clients">{t("contacts.backToList")}</Link>
      </div>
    );
  }
  const { contact, notes, relations } = card.data;

  return (
    <div className="page">
      <Link to="/app/clients" className="small">
        ← {t("contacts.backToList")}
      </Link>
      <header className="page-header">
        <div className="contact-head">
          <span className="avatar" aria-hidden="true">
            {initials(contact.full_name)}
          </span>
          <div>
            <h1>{contact.full_name}</h1>
            {contact.full_name_latin && (
              <div className="muted">
                {contact.full_name_latin} · {t("contacts.asInPassport")}
              </div>
            )}
          </div>
        </div>
        <div className="actions">
          {telegramUrl(contact.telegram) && (
            <a className="btn" href={telegramUrl(contact.telegram)!} target="_blank" rel="noreferrer">
              {t("contacts.writeTelegram")}
            </a>
          )}
          {!editing && (
            <button type="button" className="btn btn-p" onClick={() => setEditing(true)}>
              {t("reference.edit")}
            </button>
          )}
        </div>
      </header>

      {editing && (
        <ContactForm
          contact={contact}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            void refresh();
          }}
        />
      )}

      <Hints hints={card.data.hints} />
      {workspace.data?.modules.includes("tasks") && <AttentionList contactId={contact.id} />}

      <div className="card-grid">
        <div className="column">
          <ContactDetails card={card.data} />
          <QuestionnaireCard card={card.data} />
          <Paperwork card={card.data} fileStorage={workspace.data?.file_storage === true} />
          <Relations contactId={contact.id} relations={relations} />
        </div>
        <div className="column">
          {card.data.cases !== null && (
            <Cases card={card.data} creating={creatingCase} onCreating={setCreatingCase} />
          )}
          {workspace.data?.modules.includes("vault") && (
            <ContactVault contactId={contact.id} cases={card.data.cases ?? []} />
          )}
          <Consultations
            card={card.data}
            onCreateCase={card.data.cases !== null ? setCreatingCase : undefined}
          />
          <Timeline contactId={contact.id} notes={notes} />
        </div>
      </div>
    </div>
  );
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="kv">
      <span className="muted">{label}</span>
      <span>{children || "—"}</span>
    </div>
  );
}

function ContactDetails({ card }: { card: ContactCard }) {
  const { t, i18n } = useTranslation();
  const lists = useContactLists();
  const channels = useMemo(() => byId(lists.channels), [lists.channels]);
  const partners = useMemo(() => byId(lists.partners), [lists.partners]);
  const c = card.contact;
  const telegram = telegramUrl(c.telegram);
  return (
    <section className="card">
      <h2 className="h2">{t("contacts.details")}</h2>
      <Row label={t("contacts.phone")}>
        {c.phone && <a href={`tel:${c.phone}`}>{formatPhone(c.phone)}</a>}
      </Row>
      <Row label={t("contacts.telegram")}>
        {c.telegram &&
          (telegram ? (
            <a href={telegram} target="_blank" rel="noreferrer">
              @{c.telegram}
            </a>
          ) : (
            c.telegram
          ))}
      </Row>
      {c.messenger && (
        <Row label={t(`contacts.messengers.${c.messenger}`)}>{c.messenger_contact}</Row>
      )}
      <Row label={t("auth.email")}>{c.email}</Row>
      <Row label={t("contacts.birthDate")}>{formatDate(c.birth_date, i18n.language)}</Row>
      <Row label={t("contacts.channel")}>{channels.get(c.lead_channel_id ?? "")?.name}</Row>
      <Row label={t("contacts.partner")}>{partners.get(c.partner_id ?? "")?.name}</Row>
      <Row label={t("contacts.firstContact")}>
        {new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium" }).format(
          new Date(c.created_at),
        )}
      </Row>
    </section>
  );
}

function Paperwork({ card, fileStorage }: { card: ContactCard; fileStorage: boolean }) {
  const { t, i18n } = useTranslation();
  const c = card.contact;
  // A paper with a file but no date yet is not flagged as missing.
  const hasFiles = (kind: DocumentKind) => card.documents.some((d) => d.kind === kind);
  const files = (kind: DocumentKind) => (
    <DocumentFiles
      contactId={c.id}
      kind={kind}
      documents={card.documents.filter((d) => d.kind === kind)}
      fileStorage={fileStorage}
    />
  );
  return (
    <section className="card">
      <h2 className="h2">{t("contacts.paperwork")}</h2>
      <Row label={t("contacts.consent")}>
        {c.consent_signed_on ? (
          t("contacts.signedOn", { date: formatDate(c.consent_signed_on, i18n.language) })
        ) : (
          !hasFiles("consent") && (
            <span className="chip st-orange">{t("contacts.consentMissing")}</span>
          )
        )}
      </Row>
      {files("consent")}
      <Row label={t("contacts.contract")}>
        {c.contract_signed_on || c.contract_number ? (
          [
            c.contract_number && `№ ${c.contract_number}`,
            c.contract_signed_on && formatDate(c.contract_signed_on, i18n.language),
          ]
            .filter(Boolean)
            .join(" · ")
        ) : (
          !hasFiles("contract") && (
            <span className="chip st-orange">{t("contacts.contractMissing")}</span>
          )
        )}
      </Row>
      {files("contract")}
      <p className="muted small">
        {fileStorage ? t("contacts.documentsHint") : t("contacts.documentsOff")}
      </p>
    </section>
  );
}

const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024;
const DOCUMENT_TYPES = [
  ".pdf,.jpg,.jpeg,.png,.webp,.heic,.heif",
  "application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif",
].join(",");

/** Files of one paper: download, attach, delete. The server checks the type by content
 * and keeps the file encrypted; every download goes to the audit log. */
function DocumentFiles({
  contactId,
  kind,
  documents,
  fileStorage,
}: {
  contactId: string;
  kind: DocumentKind;
  documents: ContactDocument[];
  fileStorage: boolean;
}) {
  const { t, i18n } = useTranslation();
  const me = useMe();
  const queryClient = useQueryClient();
  const [error, setError] = useState<unknown>(null);
  // A new input after each pick: choosing the same file again fires onChange.
  const [inputKey, setInputKey] = useState(0);
  const refresh = () => queryClient.invalidateQueries({ queryKey: contactKey(contactId) });
  const url = `/contacts/${contactId}/documents`;

  const add = useMutation({
    mutationFn: async (file: File) =>
      apiPost<ContactDocument>(url, {
        kind,
        file_name: file.name.slice(-255),
        content: await readBase64(file),
      }),
    onSuccess: () => void refresh(),
    onError: setError,
  });
  const download = useMutation({
    mutationFn: (document: ContactDocument) =>
      apiDownload(`${url}/${document.id}`, document.file_name),
    onError: setError,
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiDelete<void>(`${url}/${id}`),
    onSuccess: () => void refresh(),
    onError: setError,
  });

  const choose = (file: File | undefined) => {
    setInputKey((key) => key + 1);
    setError(null);
    if (!file) return;
    if (file.size > MAX_DOCUMENT_BYTES) {
      setError(new ApiError(413, "document_too_large"));
      return;
    }
    add.mutate(file);
  };
  const mayDelete = (document: ContactDocument) =>
    me.data?.role === "specialist" || document.uploaded_by === me.data?.id;
  const added = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium" });

  if (documents.length === 0 && !fileStorage) return null;
  return (
    <div className="documents">
      {documents.length > 0 && (
        <ul className="plain-list">
          {documents.map((document) => (
            <li key={document.id}>
              <span>
                <button
                  type="button"
                  className="link-button"
                  title={t("contacts.downloadDocument")}
                  disabled={download.isPending}
                  onClick={() => {
                    setError(null);
                    download.mutate(document);
                  }}
                >
                  {document.file_name}
                </button>
                <span className="muted small">
                  {" · "}
                  {formatSize(document.size_bytes, i18n.language)} ·{" "}
                  {added.format(new Date(document.created_at))}
                </span>
              </span>
              {mayDelete(document) && (
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={remove.isPending}
                  onClick={() => {
                    setError(null);
                    const name = document.file_name;
                    if (window.confirm(t("contacts.confirmDeleteDocument", { name })))
                      remove.mutate(document.id);
                  }}
                >
                  {t("contacts.deleteDocument")}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {fileStorage && (
        <label className="btn btn-sm file-button">
          {add.isPending ? t("contacts.uploadingDocument") : t(`contacts.attach.${kind}`)}
          <input
            key={inputKey}
            type="file"
            className="visually-hidden"
            accept={DOCUMENT_TYPES}
            disabled={add.isPending}
            onChange={(e) => choose(e.target.files?.[0])}
          />
        </label>
      )}
      {error !== null && <ErrorAlert error={error} />}
    </div>
  );
}

function Relations({
  contactId,
  relations,
}: {
  contactId: string;
  relations: ContactCard["relations"];
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["contacts"] });
  const unlink = useMutation({
    mutationFn: (relatedId: string) =>
      apiDelete<void>(`/contacts/${contactId}/relations/${relatedId}`),
    onSuccess: () => void refresh(),
  });

  return (
    <section className="card">
      <h2 className="h2">{t("contacts.relations")}</h2>
      {relations.length === 0 && !adding && <p className="muted small">{t("contacts.noRelations")}</p>}
      {relations.length > 0 && (
        <ul className="plain-list">
          {relations.map((r) => (
            <li key={r.related_id}>
              <span>
                <Link to={`/app/clients/${r.related_id}`}>{r.full_name}</Link>
                {r.label && <span className="muted"> · {r.label}</span>}
              </span>
              <button
                type="button"
                className="btn btn-sm"
                disabled={unlink.isPending}
                onClick={() => unlink.mutate(r.related_id)}
              >
                {t("contacts.unlink")}
              </button>
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <LinkForm
          contactId={contactId}
          exclude={new Set([contactId, ...relations.map((r) => r.related_id)])}
          onDone={() => {
            setAdding(false);
            void refresh();
          }}
        />
      ) : (
        <button type="button" className="btn btn-sm" onClick={() => setAdding(true)}>
          {t("contacts.addRelation")}
        </button>
      )}
    </section>
  );
}

function LinkForm({
  contactId,
  exclude,
  onDone,
}: {
  contactId: string;
  exclude: Set<string>;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<ContactListItem | null>(null);
  const [label, setLabel] = useState("");
  const [reverse, setReverse] = useState("");
  const found = useQuery({
    queryKey: ["contacts", "search", query.trim(), "link"],
    queryFn: () =>
      apiPost<{ items: ContactListItem[] }>("/contacts/search", { query: query.trim() }),
    enabled: query.trim().length >= 2 && picked === null,
  });
  const link = useMutation({
    mutationFn: () =>
      apiPost(`/contacts/${contactId}/relations`, {
        related_id: picked?.id,
        label: label.trim() || null,
        reverse_label: reverse.trim() || null,
      }),
    onSuccess: onDone,
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    link.mutate();
  };

  return (
    <form className="form-card" onSubmit={submit}>
      {picked ? (
        <p>
          <strong>{picked.full_name}</strong>{" "}
          <button type="button" className="link-button" onClick={() => setPicked(null)}>
            {t("contacts.pickAnother")}
          </button>
        </p>
      ) : (
        <>
          <label className="field">
            <span>{t("contacts.findContact")}</span>
            <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
          {found.data && (
            <ul className="plain-list">
              {found.data.items
                .filter((c) => !exclude.has(c.id))
                .slice(0, 8)
                .map((c) => (
                  <li key={c.id}>
                    <span>{c.full_name}</span>
                    <button type="button" className="btn btn-sm" onClick={() => setPicked(c)}>
                      {t("contacts.pick")}
                    </button>
                  </li>
                ))}
            </ul>
          )}
        </>
      )}
      <div className="form-grid">
        <label className="field">
          <span>{t("contacts.relationLabel")}</span>
          <input maxLength={100} value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("contacts.reverseLabel")}</span>
          <input maxLength={100} value={reverse} onChange={(e) => setReverse(e.target.value)} />
        </label>
      </div>
      {link.isError && <ErrorAlert error={link.error} />}
      <div className="form-actions">
        <button type="submit" className="btn btn-sm btn-p" disabled={!picked || link.isPending}>
          {t("contacts.link")}
        </button>
        <button type="button" className="btn btn-sm" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

function Timeline({ contactId, notes }: { contactId: string; notes: Note[] }) {
  const { t, i18n } = useTranslation();
  const me = useMe();
  const queryClient = useQueryClient();
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<{ timezone: string }>("/workspace"),
  });
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<{ id: string; body: string } | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: contactKey(contactId) });
  const url = `/contacts/${contactId}/notes`;

  // The field is emptied on send, not on success: text typed while saving is kept.
  // On an error the sent text comes back into the field.
  const add = useMutation({
    mutationFn: (body: string) => apiPost<Note>(url, { body }),
    onSuccess: () => void refresh(),
    onError: (_error, body) => setDraft((current) => (current.trim() ? current : body)),
  });
  const edit = useMutation({
    mutationFn: (note: { id: string; body: string }) =>
      apiPatch<Note>(`${url}/${note.id}`, { body: note.body }),
    onSuccess: () => {
      setEditing(null);
      void refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiDelete<void>(`${url}/${id}`),
    onSuccess: () => void refresh(),
  });

  const when = new Intl.DateTimeFormat(i18n.language, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: workspace.data?.timezone,
  });
  const mayChange = (note: Note) =>
    me.data?.role === "specialist" || note.author_id === me.data?.id;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body = draft;
    setDraft("");
    add.mutate(body);
  };

  return (
    <section className="card timeline">
      <h2 className="h2">{t("contacts.timeline")}</h2>
      <form className="form-card" onSubmit={submit}>
        <label className="field">
          <span className="visually-hidden">{t("contacts.newNote")}</span>
          <textarea
            rows={3}
            maxLength={5000}
            placeholder={t("contacts.notePlaceholder")}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        </label>
        {add.isError && <ErrorAlert error={add.error} />}
        <div className="form-actions">
          <button type="submit" className="btn btn-sm btn-p" disabled={add.isPending || !draft.trim()}>
            {t("contacts.addNote")}
          </button>
        </div>
      </form>
      {notes.length === 0 && <p className="muted small">{t("contacts.noNotes")}</p>}
      <ol className="notes">
        {notes.map((note) => (
          <li key={note.id}>
            <div className="muted small">
              {when.format(new Date(note.created_at))}
              {note.author_email && ` · ${note.author_email}`}
              {note.edited_at && ` · ${t("contacts.edited")}`}
            </div>
            {editing?.id === note.id ? (
              <form
                className="form-card"
                onSubmit={(e) => {
                  e.preventDefault();
                  edit.mutate(editing);
                }}
              >
                <textarea
                  aria-label={t("contacts.newNote")}
                  rows={3}
                  maxLength={5000}
                  value={editing.body}
                  onChange={(e) => setEditing({ id: note.id, body: e.target.value })}
                />
                <div className="form-actions">
                  <button type="submit" className="btn btn-sm btn-p" disabled={edit.isPending || !editing.body.trim()}>
                    {t("common.save")}
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => setEditing(null)}>
                    {t("common.cancel")}
                  </button>
                </div>
              </form>
            ) : (
              <p className="note-body">{note.body}</p>
            )}
            {mayChange(note) && editing?.id !== note.id && (
              <div className="note-actions">
                <button
                  type="button"
                  className="link-button"
                  onClick={() => setEditing({ id: note.id, body: note.body })}
                >
                  {t("contacts.editNote")}
                </button>
                <button
                  type="button"
                  className="link-button"
                  disabled={remove.isPending}
                  onClick={() => {
                    if (window.confirm(t("contacts.confirmDeleteNote"))) remove.mutate(note.id);
                  }}
                >
                  {t("contacts.deleteNote")}
                </button>
              </div>
            )}
          </li>
        ))}
      </ol>
      {(edit.isError || remove.isError) && <ErrorAlert error={edit.error ?? remove.error} />}
    </section>
  );
}

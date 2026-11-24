import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { ApiError, errorCode } from "../../api";
import { LanguageSwitch } from "../../components/LanguageSwitch";
import { readBase64 } from "../../files";
import {
  MAX_PORTAL_FILE_BYTES,
  PORTAL_FILE_TYPES,
  useAskCode,
  useClientDelete,
  useClientLogin,
  useClientLogout,
  useClientPage,
  useClientUpload,
  useGiveConsent,
  type ClientItem,
  type ClientPage,
  type CodeChannel,
  type ConsentGivenBy,
} from "../../portal";

// The client's page of a case (backlog 2.9): /u/<token>, opened from the link the specialist
// sent. Nobody here has a CRM account: the client enters the code from the specialist, acknowledges the
// data notice once and uploads documents to the items of the case's checklist. The client
// sees names and statuses of their files, never the files themselves. Built to open inside
// Telegram as a Mini App later without changes: one column, phone first, no CRM shell.

function Alert({ error }: { error: unknown }) {
  const { t } = useTranslation();
  return (
    <div className="alert alert-error" role="alert">
      {t(`errors.${errorCode(error)}`, { defaultValue: t("errors.unknown") })}
    </div>
  );
}

// A link's token has one shape (backend/app/core/portal.py). Anything else never reaches a
// request: a path like «..%2Fauth» would otherwise send the page's requests elsewhere.
const TOKEN = /^[A-Za-z0-9_-]{43}$/;

export function PortalPage() {
  const { token = "" } = useParams();
  const { t, i18n } = useTranslation();
  const lang = i18n.language === "en" ? "en" : "ru";
  const valid = TOKEN.test(token);
  const page = useClientPage(token, lang, valid);
  const status = page.error instanceof ApiError ? page.error.status : null;

  let body;
  if (!valid) body = <p>{t("clientPortal.linkInvalid")}</p>;
  else if (page.isPending) body = <p className="muted">{t("common.loading")}</p>;
  else if (status === 401) body = <CodeForm token={token} />;
  else if (status === 404) body = <p>{t("clientPortal.linkInvalid")}</p>;
  else if (page.isError) body = <Alert error={page.error} />;
  else body = <Documents token={token} page={page.data} />;

  return (
    <div className="portal-page">
      <header className="portal-header">
        <span className="portal-brand">{t("clientPortal.title")}</span>
        <LanguageSwitch className="link-button" />
      </header>
      <main className="portal-main">{body}</main>
    </div>
  );
}

function CodeForm({ token }: { token: string }) {
  const { t } = useTranslation();
  const ask = useAskCode(token);
  const login = useClientLogin(token);
  const [code, setCode] = useState("");
  const [channel, setChannel] = useState<CodeChannel | null>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate(code.trim());
  };

  return (
    <form className="card portal-card" onSubmit={submit} noValidate>
      <h1 className="h2">{t("clientPortal.codeTitle")}</h1>
      <p className="muted">{t("clientPortal.codeHint")}</p>
      <button
        type="button"
        className="btn"
        disabled={ask.isPending}
        onClick={() => ask.mutate(undefined, { onSuccess: (r) => setChannel(r.channel) })}
      >
        {t("clientPortal.askCode")}
      </button>
      {channel === "email" && (
        <div className="alert alert-ok" role="status">
          {t("clientPortal.codeSentByEmail")}
        </div>
      )}
      {channel === "specialist" && (
        <div className="alert alert-warn" role="status">
          {t("clientPortal.askSpecialist")}
        </div>
      )}
      {ask.isError && <Alert error={ask.error} />}
      <label className="field">
        <span>{t("clientPortal.code")}</span>
        <input
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
        />
      </label>
      {login.isError && <Alert error={login.error} />}
      <button
        type="submit"
        className="btn btn-p btn-block"
        disabled={login.isPending || code.length !== 6}
      >
        {t("clientPortal.enter")}
      </button>
    </form>
  );
}

function Documents({ token, page }: { token: string; page: ClientPage }) {
  const { t, i18n } = useTranslation();
  const logout = useClientLogout(token);
  const country = i18n.language === "en" ? page.country.name_en : page.country.name_ru;

  return (
    <>
      <section className="card portal-card">
        <h1 className="h2">{t("clientPortal.documentsFor", { country })}</h1>
        <p className="muted">{t("clientPortal.documentsHint")}</p>
      </section>
      {!page.ready ? (
        <section className="card portal-card">
          <p>{t("clientPortal.notReady")}</p>
        </section>
      ) : page.consent.needed ? (
        <Consent token={token} page={page} />
      ) : (
        <ul className="portal-items">
          {page.items.map((item) => (
            <Item key={item.id} token={token} item={item} />
          ))}
        </ul>
      )}
      <footer className="portal-footer">
        {page.consent.withdraw_email && (
          <a
            className="link-button small"
            href={`mailto:${page.consent.withdraw_email}?subject=${encodeURIComponent(
              t("clientPortal.withdrawSubject"),
            )}`}
          >
            {t("clientPortal.withdraw")}
          </a>
        )}
        <button type="button" className="link-button small" onClick={() => logout.mutate()}>
          {t("clientPortal.logout")}
        </button>
      </footer>
    </>
  );
}

function Consent({ token, page }: { token: string; page: ClientPage }) {
  const { t } = useTranslation();
  const give = useGiveConsent(token);
  const [read, setRead] = useState(false);
  const [special, setSpecial] = useState(false);
  const [givenBy, setGivenBy] = useState<ConsentGivenBy>("applicant");
  const [representative, setRepresentative] = useState("");
  const notice = page.consent.notice;
  if (!notice) return null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    give.mutate({
      notice_id: notice.id,
      special_categories: special,
      given_by: givenBy,
      representative_name: givenBy === "representative" ? representative.trim() : null,
    });
  };

  return (
    <form className="card portal-card" onSubmit={submit}>
      <h2 className="h3">{t("clientPortal.noticeTitle")}</h2>
      <div className="portal-notice" lang={notice.language}>
        {notice.body}
      </div>
      <fieldset className="portal-choice">
        <legend>{t("clientPortal.who")}</legend>
        <label className="checkbox">
          <input
            type="radio"
            name="given_by"
            checked={givenBy === "applicant"}
            onChange={() => setGivenBy("applicant")}
          />
          {t("clientPortal.whoApplicant")}
        </label>
        <label className="checkbox">
          <input
            type="radio"
            name="given_by"
            checked={givenBy === "representative"}
            onChange={() => setGivenBy("representative")}
          />
          {t("clientPortal.whoRepresentative")}
        </label>
      </fieldset>
      {givenBy === "representative" && (
        <label className="field">
          <span>{t("clientPortal.representativeName")}</span>
          <input
            required
            maxLength={200}
            value={representative}
            onChange={(e) => setRepresentative(e.target.value)}
          />
        </label>
      )}
      <label className="checkbox">
        <input type="checkbox" checked={read} onChange={(e) => setRead(e.target.checked)} />
        {t("clientPortal.noticeRead")}
      </label>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={special}
          onChange={(e) => setSpecial(e.target.checked)}
        />
        {t("clientPortal.specialConsent")}
      </label>
      {give.isError && <Alert error={give.error} />}
      <button
        type="submit"
        className="btn btn-p btn-block"
        disabled={
          give.isPending || !read || (givenBy === "representative" && !representative.trim())
        }
      >
        {t("clientPortal.continue")}
      </button>
    </form>
  );
}

const ITEM_CHIP: Record<ClientItem["status"], string> = {
  to_upload: "chip st-gray",
  pending: "chip st-orange",
  accepted: "chip st-green",
  returned: "chip st-red",
};

function Item({ token, item }: { token: string; item: ClientItem }) {
  const { t, i18n } = useTranslation();
  const upload = useClientUpload(token, item.id);
  const remove = useClientDelete(token);
  const [inputKey, setInputKey] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const added = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium" });

  const choose = async (files: FileList | null) => {
    setInputKey((key) => key + 1);
    setError(null);
    for (const file of Array.from(files ?? [])) {
      if (file.size > MAX_PORTAL_FILE_BYTES) {
        setError(new ApiError(413, "document_too_large_20"));
        continue;
      }
      try {
        await upload.mutateAsync({
          file_name: file.name.slice(-255),
          content: await readBase64(file),
        });
      } catch (e) {
        setError(e);
      }
    }
  };

  return (
    <li className="card portal-item">
      <div className="package-line">
        <strong>{item.name}</strong>
        <span className={ITEM_CHIP[item.status]}>{t(`clientPortal.status.${item.status}`)}</span>
      </div>
      {!item.required && <p className="muted small">{t("clientPortal.ifApplies")}</p>}
      {item.note && <p className="muted small portal-note">{item.note}</p>}
      {item.files.length > 0 && (
        <ul className="plain-list">
          {item.files.map((file) => (
            <li key={file.id}>
              <span>
                {file.file_name}
                <span className="muted small"> · {added.format(new Date(file.created_at))}</span>
                {file.status === "returned" && (
                  <span className="portal-reason">
                    {file.return_reason === "other"
                      ? file.return_note
                      : t(`portal.reason.${file.return_reason}`)}
                    {file.return_reason !== "other" && file.return_note
                      ? ` · ${file.return_note}`
                      : ""}
                  </span>
                )}
              </span>
              {file.deletable && (
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(file.id, { onError: setError })}
                >
                  {t("contacts.deleteDocument")}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {item.status !== "accepted" && (
        <label className="btn btn-p file-button">
          {upload.isPending ? t("contacts.uploadingDocument") : t("clientPortal.upload")}
          <input
            key={inputKey}
            type="file"
            multiple
            className="visually-hidden"
            accept={PORTAL_FILE_TYPES}
            disabled={upload.isPending}
            onChange={(e) => void choose(e.target.files)}
          />
        </label>
      )}
      {error !== null && <Alert error={error} />}
    </li>
  );
}

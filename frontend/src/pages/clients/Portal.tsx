import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useConfirm } from "../../components/ConfirmDialog";
import { formatDate, type VisaCase } from "../../contacts";
import {
  portalUrl,
  useNewCode,
  usePortal,
  useRevokeLink,
} from "../../portal";
import { ErrorAlert } from "./ui";

// «Страница для клиента» in the case (backlog 2.9): the link the specialist sends the
// client and the code the client enters on it, shown here to pass on in any messenger or
// sent to the email of the card. The code stays while the documents are collected; only
// «Новый код» replaces it. Once the case is handed over for review, link and code are gone.
// The files the client uploads appear at the items of the checklist below.

export function CasePortal({ visaCase: c }: { visaCase: VisaCase }) {
  const { t, i18n } = useTranslation();
  const confirm = useConfirm();
  const portal = usePortal(c.id);
  const newCode = useNewCode(c.id);
  const revoke = useRevokeLink(c.id);
  const [copied, setCopied] = useState(false);
  const [emailed, setEmailed] = useState(false);

  if (portal.isPending) return null;
  if (portal.isError) return <ErrorAlert error={portal.error} />;
  const p = portal.data;
  if (!p.open || p.token === null) {
    return <p className="muted small portal-block">{t("portal.closed")}</p>;
  }
  const url = portalUrl(p.token);
  const error = newCode.error ?? revoke.error;

  const copy = async () => {
    await navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="portal-block">
      <h5 className="h4">{t("portal.title")}</h5>
      {!p.controller_ready && (
        <div className="alert alert-warn" role="status">
          {t("portal.controllerMissing")} <Link to="/settings">{t("settings.title")}</Link>
        </div>
      )}
      <div className="portal-link">
        <input readOnly value={url} aria-label={t("portal.link")} onFocus={(e) => e.target.select()} />
        <button type="button" className="btn btn-sm" onClick={() => void copy()}>
          {copied ? t("admin.copied") : t("admin.copy")}
        </button>
      </div>
      <p className="muted small">{t("portal.linkHint")}</p>

      {p.code ? (
        <div className="issued" role="status">
          <p>
            {t("portal.codeIs")} <strong className="portal-code">{p.code}</strong>
          </p>
          <p className="muted small">
            {p.code_blocked ? t("portal.codeBlocked") : t("portal.codeStays")}
          </p>
        </div>
      ) : (
        <p className="muted small">{t("portal.noCode")}</p>
      )}
      {emailed && (
        <div className="alert alert-ok" role="status">
          {t("portal.codeSent")}
        </div>
      )}
      <div className="form-actions">
        <button
          type="button"
          className={p.code && !p.code_blocked ? "btn btn-sm" : "btn btn-sm btn-p"}
          disabled={newCode.isPending}
          onClick={async () => {
            // A code already passed on stops working: asked once more.
            if (
              p.code &&
              !p.code_blocked &&
              !(await confirm({ message: t("portal.confirmNewCode"), action: t("portal.newCode") }))
            ) {
              return;
            }
            setEmailed(false);
            newCode.mutate("specialist");
          }}
        >
          {t("portal.newCode")}
        </button>
        {p.has_email && (
          <button
            type="button"
            className="btn btn-sm"
            disabled={newCode.isPending}
            onClick={() => newCode.mutate("email", { onSuccess: () => setEmailed(true) })}
          >
            {t("portal.emailCode")}
          </button>
        )}
        <button
          type="button"
          className="btn btn-sm"
          disabled={revoke.isPending}
          onClick={async () => {
            const yes = await confirm({
              message: t("portal.confirmRevoke"),
              action: t("portal.revoke"),
            });
            if (yes) revoke.mutate(undefined, { onSuccess: () => setEmailed(false) });
          }}
        >
          {t("portal.revoke")}
        </button>
      </div>
      {p.consent && (
        <p className="muted small">
          {t("portal.consentGiven", {
            date: formatDate(p.consent.given_at.slice(0, 10), i18n.language),
            version: p.consent.notice_version,
          })}
          {" · "}
          {p.consent.special_categories ? t("portal.specialYes") : t("portal.specialNo")}
          {p.consent.given_by === "representative" &&
            ` · ${t("portal.byRepresentative", { name: p.consent.representative_name })}`}
        </p>
      )}
      {error && <ErrorAlert error={error} />}
    </div>
  );
}

import { useState } from "react";
import { useTranslation } from "react-i18next";

/** A temporary password is shown once: it is handed over to the person in person. */
export type IssuedPassword = { email: string; password: string };

export function IssuedPasswordCard({
  issued,
  onClose,
}: {
  issued: IssuedPassword;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(issued.password);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section className="card issued" role="status">
      <h2 className="h2">{t("admin.issuedTitle")}</h2>
      <p>{t("admin.issuedText", { email: issued.email })}</p>
      <div className="issued-row">
        <code className="issued-password">{issued.password}</code>
        <button type="button" className="btn btn-sm" onClick={() => void copy()}>
          {copied ? t("admin.copied") : t("admin.copy")}
        </button>
      </div>
      <p className="muted small">{t("admin.issuedWarning")}</p>
      <button type="button" className="btn btn-sm" onClick={onClose}>
        {t("admin.issuedDone")}
      </button>
    </section>
  );
}

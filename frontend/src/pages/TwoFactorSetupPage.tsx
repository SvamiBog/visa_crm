import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { apiGet, apiPost, errorCode } from "../api";
import { homePath, ME_KEY, useMe, type Me } from "../auth";
import { Logo } from "../components/Logo";

type Setup = { secret: string; otpauth_uri: string; qr_code: string };

/** Connect an authenticator app (QR code), confirm with one code, save recovery codes. */
export function TwoFactorSetupPage() {
  const { t } = useTranslation();
  const me = useMe();
  const [codes, setCodes] = useState<string[] | null>(null);

  if (me.data?.two_factor_enabled && !codes) {
    return (
      <div className="auth-screen">
        <div className="card auth-card">
          <Logo />
          <h1 className="auth-title">{t("twoFactor.setupTitle")}</h1>
          <p>{t("twoFactor.alreadyOn")}</p>
          <Link to={homePath(me.data)} className="btn btn-block">
            {t("common.back")}
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-screen">
      {codes ? <RecoveryCodes codes={codes} /> : <ConnectApp onEnabled={setCodes} />}
    </div>
  );
}

function ConnectApp({ onEnabled }: { onEnabled: (codes: string[]) => void }) {
  const { t } = useTranslation();
  const [code, setCode] = useState("");
  // A query (not an effect) so a double render in development does not start setup twice.
  const setup = useQuery({
    queryKey: ["two-factor-setup"],
    queryFn: () => apiPost<Setup>("/auth/two-factor/setup"),
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
  });
  const enable = useMutation({
    mutationFn: () => apiPost<{ recovery_codes: string[] }>("/auth/two-factor/enable", { code }),
    onSuccess: (result) => onEnabled(result.recovery_codes),
    onError: () => setCode(""),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    enable.mutate();
  };

  return (
    <form className="card auth-card setup-card" onSubmit={submit} noValidate>
      <Logo />
      <h1 className="auth-title">{t("twoFactor.setupTitle")}</h1>
      <p className="muted">{t("twoFactor.setupIntro")}</p>

      <ol className="steps">
        <li>{t("twoFactor.stepInstall")}</li>
        <li>{t("twoFactor.stepScan")}</li>
      </ol>

      {setup.isPending && <p className="muted">{t("common.loading")}</p>}
      {setup.isError && (
        <div className="alert alert-error" role="alert">
          {t(`errors.${errorCode(setup.error)}`, { defaultValue: t("errors.unknown") })}
        </div>
      )}
      {setup.data && (
        <div className="qr-block">
          <img src={setup.data.qr_code} alt={t("twoFactor.qrAlt")} width={200} height={200} />
          <div className="small muted">{t("twoFactor.manualKey")}</div>
          <code className="secret">{setup.data.secret.match(/.{1,4}/g)?.join(" ")}</code>
        </div>
      )}

      <label className="field">
        <span>{t("twoFactor.stepCode")}</span>
        <input
          className="code-input"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          required
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
        />
      </label>

      {enable.isError && (
        <div className="alert alert-error" role="alert">
          {t(`errors.${errorCode(enable.error)}`, { defaultValue: t("errors.unknown") })}
        </div>
      )}

      <button
        type="submit"
        className="btn btn-p btn-block"
        disabled={!setup.data || enable.isPending || code.length !== 6}
      >
        {t("twoFactor.enable")}
      </button>
    </form>
  );
}

function RecoveryCodes({ codes }: { codes: string[] }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  const text = codes.join("\n");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const download = () => {
    const url = URL.createObjectURL(new Blob([text + "\n"], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "visacrm-recovery-codes.txt";
    link.click();
    URL.revokeObjectURL(url);
  };

  const finish = async () => {
    const me = await queryClient.fetchQuery({
      queryKey: ME_KEY,
      queryFn: () => apiGet<Me>("/auth/me"),
      staleTime: 0,
    });
    navigate(homePath(me), { replace: true });
  };

  return (
    <div className="card auth-card setup-card">
      <Logo />
      <h1 className="auth-title">{t("twoFactor.codesTitle")}</h1>
      <p>{t("twoFactor.codesIntro")}</p>
      <ul className="recovery-codes">
        {codes.map((code) => (
          <li key={code}>
            <code>{code}</code>
          </li>
        ))}
      </ul>
      <div className="form-actions">
        <button type="button" className="btn btn-sm" onClick={() => void copy()}>
          {copied ? t("admin.copied") : t("admin.copy")}
        </button>
        <button type="button" className="btn btn-sm" onClick={download}>
          {t("twoFactor.download")}
        </button>
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        <span>{t("twoFactor.codesSaved")}</span>
      </label>
      <button
        type="button"
        className="btn btn-p btn-block"
        disabled={!saved}
        onClick={() => void finish()}
      >
        {t("twoFactor.continue")}
      </button>
    </div>
  );
}

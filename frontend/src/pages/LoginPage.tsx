import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate } from "react-router-dom";
import { apiPost, errorCode } from "../api";
import { homePath, ME_KEY, useMe, type Me } from "../auth";
import { LanguageSwitch } from "../components/LanguageSwitch";
import { Logo } from "../components/Logo";

type LoginResult = Me | { mfa_required: true };

export function LoginPage() {
  const { t } = useTranslation();
  const me = useMe();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [secondStep, setSecondStep] = useState(false);

  const signedIn = (user: Me) => {
    queryClient.setQueryData(ME_KEY, user);
    navigate(homePath(user), { replace: true });
  };

  const login = useMutation({
    mutationFn: () => apiPost<LoginResult>("/auth/login", { email, password }),
    onSuccess: (result) => {
      if ("mfa_required" in result) setSecondStep(true);
      else signedIn(result);
    },
  });

  if (me.data) return <Navigate to={homePath(me.data)} replace />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate();
  };

  return (
    <div className="auth-screen">
      {secondStep ? (
        <SecondStep
          onDone={signedIn}
          onRestart={() => {
            setSecondStep(false);
            setPassword("");
            login.reset();
          }}
        />
      ) : (
        <form className="card auth-card" onSubmit={submit} noValidate>
          <Logo />
          <h1 className="auth-title">{t("auth.loginTitle")}</h1>
          <p className="muted">{t("auth.loginSubtitle")}</p>

          <label className="field">
            <span>{t("auth.email")}</span>
            <input
              type="email"
              autoComplete="username"
              inputMode="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="field">
            <span>{t("auth.password")}</span>
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>

          {login.isError && (
            <div className="alert alert-error" role="alert">
              {t(`errors.${errorCode(login.error)}`, { defaultValue: t("errors.unknown") })}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-p btn-block"
            disabled={login.isPending || !email || !password}
          >
            {login.isPending ? t("auth.signingIn") : t("auth.signIn")}
          </button>
          <p className="muted small">{t("auth.noRegistration")}</p>
        </form>
      )}
      <LanguageSwitch className="link-button" />
    </div>
  );
}

/** Second login step: a code from the authenticator app or a recovery code. */
function SecondStep({ onDone, onRestart }: { onDone: (user: Me) => void; onRestart: () => void }) {
  const { t } = useTranslation();
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);

  const verify = useMutation({
    mutationFn: () => apiPost<Me>("/auth/login/verify", { code: code.trim() }),
    onSuccess: onDone,
    onError: () => setCode(""),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    verify.mutate();
  };

  const error = verify.isError ? errorCode(verify.error) : null;

  return (
    <form className="card auth-card" onSubmit={submit} noValidate>
      <Logo />
      <h1 className="auth-title">{t("twoFactor.loginTitle")}</h1>
      <p className="muted">{t(recovery ? "twoFactor.recoveryHint" : "twoFactor.codeHint")}</p>

      <label className="field">
        <span>{t(recovery ? "twoFactor.recoveryCode" : "twoFactor.code")}</span>
        <input
          key={recovery ? "recovery" : "totp"}
          className={recovery ? undefined : "code-input"}
          type="text"
          inputMode={recovery ? "text" : "numeric"}
          autoComplete="one-time-code"
          maxLength={recovery ? 32 : 6}
          required
          autoFocus
          value={code}
          onChange={(e) => setCode(recovery ? e.target.value : e.target.value.replace(/\D/g, ""))}
        />
      </label>

      {error && (
        <div className="alert alert-error" role="alert">
          {t(`errors.${error}`, { defaultValue: t("errors.unknown") })}
        </div>
      )}

      {error === "login_expired" || error === "account_locked" ? (
        <button type="button" className="btn btn-block" onClick={onRestart}>
          {t("twoFactor.startOver")}
        </button>
      ) : (
        <button
          type="submit"
          className="btn btn-p btn-block"
          disabled={verify.isPending || code.length < 6}
        >
          {t("twoFactor.confirm")}
        </button>
      )}
      <button
        type="button"
        className="link-button"
        onClick={() => {
          setRecovery(!recovery);
          setCode("");
          verify.reset();
        }}
      >
        {t(recovery ? "twoFactor.useApp" : "twoFactor.useRecovery")}
      </button>
    </form>
  );
}

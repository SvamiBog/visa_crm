import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate } from "react-router-dom";
import { apiPost, errorCode } from "../api";
import { forgetAccount, homePath, ME_KEY, useMe, type Me } from "../auth";
import { Field, useFieldErrors } from "../components/Field";
import { LanguageSwitch } from "../components/LanguageSwitch";
import { Logo } from "../components/Logo";
import { PasswordField } from "../components/PasswordField";

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
    // The page opens only when no one is signed in here, so nothing cached belongs to this
    // account: whatever the last one left goes before its screens open (audit N14).
    forgetAccount(queryClient);
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
  // The text names both fields; the password is the one typed again.
  const errors = useFieldErrors(login.error, { codes: { invalid_credentials: "password" } });

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
        <form ref={errors.form} className="card auth-card" onSubmit={submit} noValidate>
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
          <PasswordField
            label={t("auth.password")}
            autoComplete="current-password"
            value={password}
            onChange={setPassword}
            error={errors.at("password")}
          />

          {errors.general ? (
            <div className="alert alert-error" role="alert">
              {t(`errors.${errorCode(errors.general)}`, { defaultValue: t("errors.unknown") })}
            </div>
          ) : null}

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
  const errors = useFieldErrors(verify.error, { codes: { invalid_code: "code" } });

  return (
    <form ref={errors.form} className="card auth-card" onSubmit={submit} noValidate>
      <Logo />
      <h1 className="auth-title">{t("twoFactor.loginTitle")}</h1>
      <p className="muted">{t(recovery ? "twoFactor.recoveryHint" : "twoFactor.codeHint")}</p>

      <Field
        label={t(recovery ? "twoFactor.recoveryCode" : "twoFactor.code")}
        error={errors.at("code")}
      >
        {(control) => (
          <input
            {...control}
            key={recovery ? "recovery" : "totp"}
            className={recovery ? undefined : "code-input"}
            type="text"
            inputMode={recovery ? "text" : "numeric"}
            autoComplete="one-time-code"
            maxLength={recovery ? 32 : 6}
            required
            autoFocus
            value={code}
            onChange={(e) =>
              setCode(recovery ? e.target.value : e.target.value.replace(/\D/g, ""))
            }
          />
        )}
      </Field>

      {errors.general ? (
        <div className="alert alert-error" role="alert">
          {t(`errors.${errorCode(errors.general)}`, { defaultValue: t("errors.unknown") })}
        </div>
      ) : null}

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

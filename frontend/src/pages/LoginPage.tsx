import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate } from "react-router-dom";
import { apiPost, errorCode } from "../api";
import { homePath, ME_KEY, useMe, type Me } from "../auth";
import { LanguageSwitch } from "../components/LanguageSwitch";
import { Logo } from "../components/Logo";

export function LoginPage() {
  const { t } = useTranslation();
  const me = useMe();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const login = useMutation({
    mutationFn: () => apiPost<Me>("/auth/login", { email, password }),
    onSuccess: (user) => {
      queryClient.setQueryData(ME_KEY, user);
      navigate(homePath(user), { replace: true });
    },
  });

  if (me.data) return <Navigate to={homePath(me.data)} replace />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate();
  };

  return (
    <div className="auth-screen">
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
      <LanguageSwitch className="link-button" />
    </div>
  );
}

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { apiPost, errorCode } from "../api";
import { homePath, ME_KEY, useMe, type Me } from "../auth";
import { Logo } from "../components/Logo";
import { PasswordField } from "../components/PasswordField";

// Must match MIN_PASSWORD_LENGTH in backend/app/core/security.py.
const MIN_LENGTH = 10;

export function ChangePasswordPage() {
  const { t } = useTranslation();
  const me = useMe();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const change = useMutation({
    mutationFn: () =>
      apiPost<Me>("/auth/change-password", { current_password: current, new_password: next }),
    onSuccess: (user) => {
      queryClient.setQueryData(ME_KEY, user);
      navigate(homePath(user), { replace: true });
    },
  });

  const forced = me.data?.must_change_password ?? false;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (next.length < MIN_LENGTH) return setLocalError("password_too_short");
    if (next !== repeat) return setLocalError("passwords_differ");
    setLocalError(null);
    change.mutate();
  };

  const error = localError ?? (change.isError ? errorCode(change.error) : null);

  return (
    <div className="auth-screen">
      <form className="card auth-card" onSubmit={submit} noValidate>
        <Logo />
        <h1 className="auth-title">{t("auth.changePasswordTitle")}</h1>
        <p className="muted">
          {forced ? t("auth.changePasswordForced") : t("auth.changePasswordHint")}
        </p>

        <PasswordField
          label={t(forced ? "auth.temporaryPassword" : "auth.currentPassword")}
          autoComplete="current-password"
          autoFocus
          value={current}
          onChange={setCurrent}
        />
        <PasswordField
          label={t("auth.newPassword")}
          autoComplete="new-password"
          minLength={MIN_LENGTH}
          hint={t("auth.passwordRule", { count: MIN_LENGTH })}
          value={next}
          onChange={setNext}
        />
        <PasswordField
          label={t("auth.repeatPassword")}
          autoComplete="new-password"
          value={repeat}
          onChange={setRepeat}
        />

        {error && (
          <div className="alert alert-error" role="alert">
            {t(`errors.${error}`, { count: MIN_LENGTH, defaultValue: t("errors.unknown") })}
          </div>
        )}

        <button
          type="submit"
          className="btn btn-p btn-block"
          disabled={change.isPending || !current || !next || !repeat}
        >
          {t("auth.savePassword")}
        </button>
        {!forced && me.data && (
          <Link to={homePath(me.data)} className="link-button">
            {t("common.back")}
          </Link>
        )}
      </form>
    </div>
  );
}

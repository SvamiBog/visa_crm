import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiDelete, apiGet, apiPatch, apiPost, errorCode } from "../api";
import { ME_KEY, useMe, type Me } from "../auth";
import { PaymentsTable, SubscriptionChip } from "../components/Subscription";
import { formatDate } from "../contacts";
import { useOwnSubscription } from "../subscriptions";
import { useMoney } from "./clients/Finance";
import { Row } from "./clients/ui";
import { applyTheme, DEFAULT_THEME, THEMES, type UiTheme } from "../theme";

const LANGUAGES = ["ru", "en"] as const;

/** The user's own settings: login email, style and language. */
export function SettingsPage() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const me = useMe();
  const current = me.data?.ui_theme ?? DEFAULT_THEME;

  const save = useMutation({
    mutationFn: (theme: UiTheme) => apiPatch<Me>("/auth/preferences", { ui_theme: theme }),
    // Switch at once; the account keeps the choice for other devices.
    onMutate: (theme) => applyTheme(theme),
    onSuccess: (updated) => queryClient.setQueryData(ME_KEY, updated),
    onError: () => applyTheme(current),
  });

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("settings.title")}</h1>
          <div className="muted">{t("settings.subtitle")}</div>
        </div>
      </header>

      <OwnSubscription />
      <EmailChange />
      <TwoFactor />

      <section className="card form-card">
        <h2 className="h2">{t("settings.theme")}</h2>
        <p className="muted small">{t("settings.themeHint")}</p>
        <div className="theme-picker" role="radiogroup" aria-label={t("settings.theme")}>
          {THEMES.map((theme) => {
            const selected = (save.isPending ? save.variables : current) === theme;
            return (
              <label key={theme} className={selected ? "theme-option selected" : "theme-option"}>
                <input
                  type="radio"
                  name="ui-theme"
                  value={theme}
                  checked={selected}
                  onChange={() => save.mutate(theme)}
                  disabled={save.isPending}
                />
                <ThemePreview theme={theme} />
                <span className="theme-name">
                  {t(`theme.${theme}.name`)}
                  {theme === DEFAULT_THEME && (
                    <span className="chip st-gray">{t("settings.default")}</span>
                  )}
                </span>
                <span className="muted small">{t(`theme.${theme}.about`)}</span>
              </label>
            );
          })}
        </div>
        {save.isError && (
          <div className="alert alert-error" role="alert">
            {t(`errors.${errorCode(save.error)}`, { defaultValue: t("errors.unknown") })}
          </div>
        )}
      </section>

      <section className="card form-card">
        <h2 className="h2">{t("settings.language")}</h2>
        <div className="lang-options" role="radiogroup" aria-label={t("settings.language")}>
          {LANGUAGES.map((lng) => (
            <label key={lng} className="checkbox">
              <input
                type="radio"
                name="language"
                value={lng}
                checked={i18n.language === lng}
                onChange={() => void i18n.changeLanguage(lng)}
              />
              {t(`lang.${lng}`)}
            </label>
          ))}
        </div>
      </section>
    </div>
  );
}

/** The specialist's CRM subscription, read-only (roadmap 3.8); none without a tariff. */
function OwnSubscription() {
  const { t, i18n } = useTranslation();
  const me = useMe();
  const money = useMoney();
  const own = useOwnSubscription(me.data?.role === "specialist");
  const s = own.data;
  if (!s?.tariff) return null;
  const date = (value: string | null) => formatDate(value, i18n.language);
  return (
    <section className="card form-card">
      <div className="card-head">
        <h2 className="h2">{t("subscription.own")}</h2>
        <SubscriptionChip state={s} />
      </div>
      <p className="muted small">{t("subscription.ownHint")}</p>
      <div>
        <Row label={t("subscription.tariff")}>
          {s.tariff.name} ·{" "}
          {t("subscription.perMonth", { amount: `${money(s.tariff.price)} ${s.tariff.currency}` })}
        </Row>
        <Row label={t("subscription.colNextDue")}>{date(s.next_due_on)}</Row>
        {s.trial_until && (
          <Row label={t("subscription.trialUntil")}>{date(s.trial_until)}</Row>
        )}
      </div>
      <h3 className="h3">{t("subscription.payments")}</h3>
      <PaymentsTable payments={s.payments} />
    </section>
  );
}

function ErrorAlert({ error }: { error: unknown }) {
  const { t } = useTranslation();
  return (
    <div className="alert alert-error" role="alert">
      {t(`errors.${errorCode(error)}`, { defaultValue: t("errors.unknown") })}
    </div>
  );
}

/** Two-factor login is optional: turned on with the authenticator app, turned off with the
 * password and a current code (or a recovery code). */
function TwoFactor() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const me = useMe();
  const [turningOff, setTurningOff] = useState(false);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const disable = useMutation({
    mutationFn: () => apiPost<void>("/auth/two-factor/disable", { password, code }),
    onSuccess: () => {
      setTurningOff(false);
      setPassword("");
      setCode("");
      void queryClient.invalidateQueries({ queryKey: ME_KEY });
    },
    onError: () => setCode(""),
  });
  const on = me.data?.two_factor_enabled ?? false;

  return (
    <section className="card form-card">
      <h2 className="h2">{t("settings.twoFactor")}</h2>
      <p className="muted small">{t(on ? "settings.twoFactorOn" : "settings.twoFactorOff")}</p>
      {disable.isSuccess && !on && (
        <div className="alert alert-ok" role="status">
          {t("settings.twoFactorTurnedOff")}
        </div>
      )}
      {!on && (
        <div className="form-actions">
          <Link to="/setup-two-factor" className="btn btn-p">
            {t("twoFactor.turnOn")}
          </Link>
        </div>
      )}
      {on && !turningOff && (
        <div className="form-actions">
          <button type="button" className="btn" onClick={() => setTurningOff(true)}>
            {t("settings.twoFactorTurnOff")}
          </button>
        </div>
      )}
      {on && turningOff && (
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            disable.mutate();
          }}
        >
          <p className="muted small">{t("settings.twoFactorTurnOffHint")}</p>
          <div className="form-grid">
            <label className="field">
              <span>{t("auth.currentPassword")}</span>
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            <label className="field">
              <span>{t("settings.twoFactorCode")}</span>
              <input
                autoComplete="one-time-code"
                required
                minLength={6}
                maxLength={32}
                value={code}
                onChange={(e) => setCode(e.target.value.trim())}
              />
            </label>
          </div>
          {disable.isError && <ErrorAlert error={disable.error} />}
          <div className="form-actions">
            <button type="submit" className="btn btn-p" disabled={disable.isPending}>
              {t("settings.twoFactorTurnOff")}
            </button>
            <button type="button" className="btn" onClick={() => setTurningOff(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

type PendingChange = { new_email: string; expires_at: string; resend_at: string };
const PENDING_KEY = ["auth", "email-change"] as const;

/** Seconds until the given moment, ticking every second. */
function useSecondsUntil(moment: string | undefined): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return moment ? Math.max(0, Math.ceil((new Date(moment).getTime() - now) / 1000)) : 0;
}

/** The login email: a code goes to the new address, the email changes once it is entered. */
function EmailChange() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const me = useMe();
  const pending = useQuery({
    queryKey: PENDING_KEY,
    queryFn: () => apiGet<PendingChange | null>("/auth/email-change"),
  });
  const [asking, setAsking] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [changed, setChanged] = useState(false);
  const waitSeconds = useSecondsUntil(pending.data?.resend_at);

  const request = useMutation({
    mutationFn: () =>
      apiPost<PendingChange>("/auth/email-change", {
        new_email: newEmail,
        current_password: password,
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(PENDING_KEY, data);
      setAsking(false);
      setPassword("");
      setCode("");
    },
  });
  const confirm = useMutation({
    mutationFn: () => apiPost<Me>("/auth/email-change/confirm", { code }),
    onSuccess: (updated) => {
      queryClient.setQueryData(ME_KEY, updated);
      queryClient.setQueryData(PENDING_KEY, null);
      setChanged(true);
      setCode("");
    },
  });
  const cancel = useMutation({
    mutationFn: () => apiDelete<void>("/auth/email-change"),
    onSuccess: () => queryClient.setQueryData(PENDING_KEY, null),
  });

  const until = pending.data
    ? new Intl.DateTimeFormat(i18n.language, { timeStyle: "short" }).format(
        new Date(pending.data.expires_at),
      )
    : "";
  const startAsking = () => {
    setNewEmail(pending.data?.new_email ?? "");
    setChanged(false);
    request.reset();
    setAsking(true);
  };

  return (
    <section className="card form-card">
      <h2 className="h2">{t("settings.email")}</h2>
      <div className="kv">
        <span className="muted">{t("settings.currentEmail")}</span>
        <span>{me.data?.email}</span>
      </div>
      {changed && (
        <div className="alert alert-ok" role="status">
          {t("settings.emailChanged")}
        </div>
      )}

      {asking ? (
        <form
          className="form-card"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            request.mutate();
          }}
        >
          <p className="muted small">{t("settings.emailHint")}</p>
          <div className="form-grid">
            <label className="field">
              <span>{t("settings.newEmail")}</span>
              <input
                type="email"
                required
                maxLength={320}
                autoComplete="email"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
              />
            </label>
            <label className="field">
              <span>{t("auth.currentPassword")}</span>
              <input
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
          </div>
          {request.isError && <ErrorAlert error={request.error} />}
          <div className="form-actions">
            <button type="submit" className="btn btn-p" disabled={request.isPending}>
              {t("settings.sendCode")}
            </button>
            <button type="button" className="btn" onClick={() => setAsking(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      ) : pending.data ? (
        <form
          className="form-card"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            confirm.mutate();
          }}
        >
          <p>{t("settings.codeSent", { email: pending.data.new_email, time: until })}</p>
          <label className="field code-field">
            <span>{t("settings.code")}</span>
            <input
              required
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9 ]{6,7}"
              maxLength={7}
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </label>
          {confirm.isError && <ErrorAlert error={confirm.error} />}
          {cancel.isError && <ErrorAlert error={cancel.error} />}
          <div className="form-actions">
            <button type="submit" className="btn btn-p" disabled={confirm.isPending}>
              {t("settings.confirmEmail")}
            </button>
            <button type="button" className="btn" disabled={waitSeconds > 0} onClick={startAsking}>
              {waitSeconds > 0
                ? t("settings.resendIn", { count: waitSeconds })
                : t("settings.resend")}
            </button>
            <button
              type="button"
              className="btn"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate()}
            >
              {t("common.cancel")}
            </button>
          </div>
        </form>
      ) : (
        <div className="form-actions">
          <button type="button" className="btn" onClick={startAsking}>
            {t("settings.changeEmail")}
          </button>
        </div>
      )}
    </section>
  );
}

/** A small picture of the app in the given theme: menu, a card with rows and a button. */
function ThemePreview({ theme }: { theme: UiTheme }) {
  const { t } = useTranslation();
  return (
    <span className="theme-preview" data-ui-theme={theme} aria-hidden="true">
      <span className="tp-nav">
        <span className="tp-logo" />
        <span className="tp-nav-item on" />
        <span className="tp-nav-item" />
        <span className="tp-nav-item" />
      </span>
      <span className="tp-main">
        <span className="tp-title">{t("settings.sample")}</span>
        <span className="tp-card">
          <span className="tp-row">
            <span className="tp-chip" />
            <span className="tp-line" />
          </span>
          <span className="tp-row">
            <span className="tp-chip warn" />
            <span className="tp-line short" />
          </span>
        </span>
        <span className="tp-btn" />
      </span>
    </span>
  );
}

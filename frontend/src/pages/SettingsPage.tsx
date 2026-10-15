import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiPatch, errorCode } from "../api";
import { ME_KEY, useMe, type Me } from "../auth";
import { applyTheme, DEFAULT_THEME, THEMES, type UiTheme } from "../theme";

const LANGUAGES = ["ru", "en"] as const;

/** The user's own interface settings: style and language. */
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

import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { errorCode } from "../../api";
import { useCountries, useLocalName, type Country } from "../../reference";

// Small pieces shared by the client card's blocks.

export function ErrorAlert({ error }: { error: unknown }) {
  const { t } = useTranslation();
  return (
    <div className="alert alert-error" role="alert">
      {t(`errors.${errorCode(error)}`, { defaultValue: t("errors.unknown") })}
    </div>
  );
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="kv">
      <span className="muted">{label}</span>
      <span>{children || "—"}</span>
    </div>
  );
}

/** Today in the browser's calendar, as an ISO date for date inputs. */
export function todayIso(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

/** The countries reference by name in the interface's language, for selects. */
export function useSortedCountries(): Country[] {
  const name = useLocalName();
  const countries = useCountries();
  return useMemo(
    () =>
      [...(countries.data ?? [])].sort((a, b) => name.country(a).localeCompare(name.country(b))),
    [countries.data, name],
  );
}

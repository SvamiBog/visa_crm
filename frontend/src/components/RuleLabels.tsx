import { useTranslation } from "react-i18next";
import { useLocalName, type Country, type Operator, type Rule } from "../reference";

// How a country rule reads in tables: booking type, who takes applications, appeals.

export function BookingLabel({ rule }: { rule: Rule }) {
  const { t } = useTranslation();
  return (
    <>
      {t(`reference.booking.${rule.booking_type}`)}
      {rule.window_days !== null && (
        <span className="muted"> · {t("reference.windowDays", { count: rule.window_days })}</span>
      )}
    </>
  );
}

export function OperatorLabel({
  rule,
  operator,
  represented,
}: {
  rule: Rule;
  operator?: Operator;
  represented?: Country;
}) {
  const { t } = useTranslation();
  const name = useLocalName();
  const via = represented ? t("reference.via", { country: name.country(represented) }) : null;
  const who = operator ? operator.name : t("reference.embassy");
  return (
    <>
      {who}
      {via && <span className="muted"> · {via}</span>}
      {rule.notes && <div className="muted small">{rule.notes}</div>}
    </>
  );
}

export function AppealsLabel({ rule }: { rule: Rule }) {
  const { t } = useTranslation();
  if (rule.appeals_accepted === null) return <>{t("reference.unknown")}</>;
  if (!rule.appeals_accepted) return <>{t("reference.appealsNo")}</>;
  return (
    <>
      {rule.appeal_deadline_days
        ? t("reference.appealsDays", { count: rule.appeal_deadline_days })
        : t("reference.appealsYes")}
    </>
  );
}

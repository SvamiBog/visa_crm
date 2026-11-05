import { useTranslation } from "react-i18next";
import { formatDate } from "../contacts";
import { useMoney } from "../pages/clients/Finance";
import type {
  SubscriptionPayment,
  SubscriptionState,
  SubscriptionStatus,
} from "../subscriptions";

// Pieces of the CRM subscription shown both to the admin and to the specialist.

const COLOR: Record<SubscriptionStatus, string> = {
  none: "st-gray",
  trial: "st-blue",
  paid: "st-green",
  due_soon: "st-orange",
  overdue: "st-red",
};

/** «Оплачено», «Скоро оплата», «Просрочено на 5 дн.». */
export function SubscriptionChip({ state }: { state: SubscriptionState }) {
  const { t } = useTranslation();
  return (
    <span className={`chip ${COLOR[state.status]}`}>
      {t(`subscription.status.${state.status}`, { days: state.overdue_days })}
    </span>
  );
}

/** The payment history, the newest first; the admin also gets «Удалить». */
export function PaymentsTable({
  payments,
  onDelete,
  deleting,
}: {
  payments: SubscriptionPayment[];
  onDelete?: (payment: SubscriptionPayment) => void;
  deleting?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const money = useMoney();
  if (payments.length === 0) return <p className="muted small">{t("subscription.noPayments")}</p>;
  return (
    <div className="table-wrap">
      <table className="tbl">
        <thead>
          <tr>
            <th>{t("subscription.paidOn")}</th>
            <th>{t("subscription.months")}</th>
            <th>{t("subscription.amount")}</th>
            <th>{t("subscription.note")}</th>
            {onDelete && <th aria-label={t("subscription.deletePayment")} />}
          </tr>
        </thead>
        <tbody>
          {payments.map((p) => (
            <tr key={p.id}>
              <td className="nowrap">{formatDate(p.paid_on, i18n.language)}</td>
              <td className="nowrap">{t("subscription.monthsShort", { count: p.months })}</td>
              <td className="nowrap">
                {money(p.amount)} {p.currency}
              </td>
              <td className="muted">{p.note}</td>
              {onDelete && (
                <td>
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={deleting}
                    onClick={() => onDelete(p)}
                  >
                    {t("subscription.deletePayment")}
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

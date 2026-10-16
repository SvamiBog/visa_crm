import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { apiDelete, apiGet, apiPatch, apiPost, errorCode } from "../../api";
import { formatDate, type VisaCase } from "../../contacts";
import { ErrorAlert, todayIso } from "./ui";

// Case finances (backlog 1.9): everything in the specialist's base currency (BYN);
// payments and expenses in other currencies keep the National Bank's rate of their day.
// The API counts the balance, the payout, the tax and the profit (app/core/finance.py).

const TERMS = ["percent", "fixed", "none", "unspecified"] as const;
type Terms = (typeof TERMS)[number];

type Payment = {
  id: string;
  paid_on: string;
  amount: string;
  currency: string;
  rate: string;
  amount_base: string;
  official: boolean;
  note: string | null;
};
type Expense = {
  id: string;
  spent_on: string;
  amount: string;
  currency: string;
  rate: string;
  amount_base: string;
  description: string;
};
type Finance = {
  case_id: string;
  base_currency: string;
  tax_rate: string;
  price: string | null;
  prepayment: string | null;
  consultation_credit: string;
  registrar_fee_amount: string | null;
  registrar_fee_currency: string | null;
  registrar_fee_rate: string | null;
  registrar_fee_base: string | null;
  partner_terms: Terms | null;
  partner_percent: string | null;
  partner_fixed: string | null;
  partner_paid_on: string | null;
  payments: Payment[];
  expenses: Expense[];
  paid: string;
  official: string;
  balance: string | null;
  partner_payout: string | null;
  tax: string;
  expenses_total: string;
  profit: string | null;
  suggestions: {
    prepayment: string | null;
    registrar_fee: {
      amount: string;
      currency: string;
      rate: string | null;
    } | null;
    partner_terms: Terms | null;
    partner_percent: string | null;
    partner_fixed: string | null;
    consultation_credit: string | null;
  };
};

const financeKey = (caseId: string) => ["finance", caseId] as const;

/** Amounts are typed with a comma or a dot; the API takes a decimal string. */
function toDecimal(value: string): string {
  return value.trim().replace(/\s/g, "").replace(",", ".");
}

/** 1 350 or 1 350,50: kopecks only when there are any. */
export function useMoney() {
  const { i18n } = useTranslation();
  return (value: string | null | undefined) => {
    if (value === null || value === undefined) return "—";
    const number = Number(value);
    return new Intl.NumberFormat(i18n.language, {
      minimumFractionDigits: Number.isInteger(number) ? 0 : 2,
      maximumFractionDigits: 2,
    }).format(number);
  };
}

/** A rate as the bank gives it: 3,4012 or 0,77142. */
function useRateText() {
  const { i18n } = useTranslation();
  return (value: string | null) =>
    value === null
      ? "—"
      : new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 6 }).format(Number(value));
}

function usePaymentCurrencies(base: string) {
  const money = useQuery({
    queryKey: ["dictionaries", "money"],
    queryFn: () =>
      apiGet<{ base_currency: string; payment_currencies: string[] }>("/dictionaries/money"),
  });
  const list = money.data?.payment_currencies ?? [];
  return list.includes(base) ? list : [base, ...list];
}

type Saved = { currency: string; day: string; rate: string } | null;

/** The National Bank's rate for a currency on a day, for a form to fill in; the user may
 * type another. Empty for the base currency or when the bank has none. The rate already
 * saved for this currency and day (maybe corrected by hand) comes back instead. */
function useBankRate(
  currency: string,
  day: string,
  base: string,
  onRate: (rate: string) => void,
  saved: Saved,
) {
  const rate = useQuery({
    queryKey: ["rates", currency, day],
    queryFn: () => apiGet<{ rate: string }>(`/rates?currency=${currency}&on=${day}`),
    enabled: currency !== base && /^\d{4}-\d{2}-\d{2}$/.test(day),
    retry: false,
    staleTime: Infinity,
  });
  useEffect(() => {
    if (currency === base) onRate("");
    else if (saved && saved.currency === currency && saved.day === day) onRate(saved.rate);
    // Empty while the rate is on its way, so a form cannot be saved with another one.
    else onRate(rate.data?.rate ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currency, day, base, rate.data]);
  return rate;
}

export function CaseFinance({ visaCase: c }: { visaCase: VisaCase }) {
  const { t, i18n } = useTranslation();
  const money = useMoney();
  const rateText = useRateText();
  const finance = useQuery({
    queryKey: financeKey(c.id),
    queryFn: () => apiGet<Finance>(`/cases/${c.id}/finance`),
  });
  const [editing, setEditing] = useState<"finance" | "payment" | "expense" | null>(null);
  const [payment, setPayment] = useState<Payment | null>(null);
  const [expense, setExpense] = useState<Expense | null>(null);

  if (finance.isPending) return <p className="muted small">{t("common.loading")}</p>;
  if (finance.isError) return <ErrorAlert error={finance.error} />;
  const f = finance.data;
  const base = f.base_currency;
  const close = () => {
    setEditing(null);
    setPayment(null);
    setExpense(null);
  };

  return (
    <section className="finance">
      <div className="card-head">
        <h4 className="h3">{t("finance.title", { currency: base })}</h4>
        {editing === null && (
          <span className="actions">
            <button type="button" className="btn btn-sm" onClick={() => setEditing("finance")}>
              {t("reference.edit")}
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setEditing("payment")}>
              {t("finance.addPayment")}
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setEditing("expense")}>
              {t("finance.addExpense")}
            </button>
          </span>
        )}
      </div>

      {editing === "finance" && <FinanceForm finance={f} onDone={close} />}
      {editing === "payment" && <PaymentForm finance={f} payment={payment} onDone={close} />}
      {editing === "expense" && <ExpenseForm finance={f} expense={expense} onDone={close} />}

      {f.price === null && f.payments.length === 0 ? (
        <p className="muted small">{t("finance.empty")}</p>
      ) : (
        <table className="tbl money-table">
          <tbody>
            <MoneyRow label={t("finance.price")} value={money(f.price)} />
            {Number(f.consultation_credit) > 0 && (
              <MoneyRow
                label={t("finance.consultation")}
                value={`−${money(f.consultation_credit)}`}
              />
            )}
            {f.payments.map((p) => (
              <MoneyRow
                key={p.id}
                label={
                  <>
                    <PaymentLabel payment={p} base={base} />
                    <ItemActions
                      onEdit={() => {
                        setPayment(p);
                        setEditing("payment");
                      }}
                      deleteUrl={`/payments/${p.id}`}
                      caseId={c.id}
                    />
                  </>
                }
                value={`−${money(p.amount_base)}`}
              />
            ))}
            <MoneyRow strong label={t("finance.balance")} value={money(f.balance)} />
            {f.prepayment !== null && (
              <MoneyRow muted label={t("finance.prepaymentAgreed")} value={money(f.prepayment)} />
            )}
            {f.registrar_fee_base !== null && (
              <MoneyRow
                label={
                  f.registrar_fee_currency === base
                    ? t("finance.talon")
                    : t("finance.talonForeign", {
                        amount: money(f.registrar_fee_amount),
                        currency: f.registrar_fee_currency,
                        rate: rateText(f.registrar_fee_rate),
                      })
                }
                value={money(f.registrar_fee_base)}
              />
            )}
            {f.partner_terms && f.partner_terms !== "unspecified" && (
              <MoneyRow label={<PartnerLabel finance={f} />} value={money(f.partner_payout)} />
            )}
            <MoneyRow
              label={t("finance.tax", {
                rate: money(f.tax_rate),
                official: money(f.official),
              })}
              value={money(f.tax)}
            />
            {f.expenses.map((e) => (
              <MoneyRow
                key={e.id}
                label={
                  <>
                    {e.description}
                    <span className="muted">
                      {" · "}
                      {formatDate(e.spent_on, i18n.language)}
                      {e.currency !== base && ` · ${money(e.amount)} ${e.currency}`}
                    </span>
                    <ItemActions
                      onEdit={() => {
                        setExpense(e);
                        setEditing("expense");
                      }}
                      deleteUrl={`/expenses/${e.id}`}
                      caseId={c.id}
                    />
                  </>
                }
                value={money(e.amount_base)}
              />
            ))}
            <MoneyRow strong label={t("finance.profit")} value={money(f.profit)} />
          </tbody>
        </table>
      )}
    </section>
  );
}

function MoneyRow({
  label,
  value,
  strong = false,
  muted = false,
}: {
  label: ReactNode;
  value: string;
  strong?: boolean;
  muted?: boolean;
}) {
  return (
    <tr className={strong ? "money-total" : muted ? "muted" : undefined}>
      <td>{label}</td>
      <td className="mono money-cell">{value}</td>
    </tr>
  );
}

function PaymentLabel({ payment: p, base }: { payment: Payment; base: string }) {
  const { t, i18n } = useTranslation();
  const money = useMoney();
  const rateText = useRateText();
  return (
    <>
      {t("finance.payment", { date: formatDate(p.paid_on, i18n.language) })}
      {p.currency !== base && (
        <span className="muted">
          {" · "}
          {t("finance.atRate", {
            amount: money(p.amount),
            currency: p.currency,
            rate: rateText(p.rate),
          })}
        </span>
      )}
      {p.official && <span className="chip st-green finance-chip">{t("finance.official")}</span>}
      {p.note && <span className="muted"> · {p.note}</span>}
    </>
  );
}

function PartnerLabel({ finance: f }: { finance: Finance }) {
  const { t, i18n } = useTranslation();
  const money = useMoney();
  const how =
    f.partner_terms === "percent"
      ? t("finance.partnerPercent", { percent: money(f.partner_percent) })
      : f.partner_terms === "fixed"
        ? t("finance.partnerFixed")
        : t("finance.partnerNone");
  const paid = f.partner_terms === "percent" || f.partner_terms === "fixed";
  return (
    <>
      {t("finance.partner")} · {how}
      {paid &&
        (f.partner_paid_on ? (
          <span className="chip st-green finance-chip">
            {t("finance.partnerPaid", {
              date: formatDate(f.partner_paid_on, i18n.language),
            })}
          </span>
        ) : (
          <span className="chip st-orange finance-chip">{t("finance.toPay")}</span>
        ))}
    </>
  );
}

function ItemActions({
  onEdit,
  deleteUrl,
  caseId,
}: {
  onEdit: () => void;
  deleteUrl: string;
  caseId: string;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => apiDelete<Finance>(deleteUrl),
    onSuccess: (data) => queryClient.setQueryData(financeKey(caseId), data),
  });
  return (
    <span className="finance-actions">
      <button type="button" className="link-button" onClick={onEdit}>
        {t("reference.edit")}
      </button>
      <button
        type="button"
        className="link-button"
        disabled={remove.isPending}
        onClick={() => {
          if (window.confirm(t("finance.confirmDelete"))) remove.mutate();
        }}
      >
        {t("vault.delete")}
      </button>
    </span>
  );
}

/** Saves a form; the API answers with the case's finance counted again. */
function useSave(caseId: string, onDone: () => void, request: () => Promise<Finance>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: request,
    onSuccess: (data: Finance) => {
      queryClient.setQueryData(financeKey(caseId), data);
      onDone();
    },
  });
}

function FormButtons({ pending, onCancel }: { pending: boolean; onCancel: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="form-actions">
      <button type="submit" className="btn btn-sm btn-p" disabled={pending}>
        {t("common.save")}
      </button>
      <button type="button" className="btn btn-sm" onClick={onCancel}>
        {t("common.cancel")}
      </button>
    </div>
  );
}

function FinanceForm({ finance: f, onDone }: { finance: Finance; onDone: () => void }) {
  const { t } = useTranslation();
  const hints = f.suggestions;
  const base = f.base_currency;
  const currencies = usePaymentCurrencies(base);
  // Empty fields start from the lists and the consultation.
  const [price, setPrice] = useState(f.price ?? "");
  const [prepayment, setPrepayment] = useState(f.prepayment ?? hints.prepayment ?? "");
  const [credit, setCredit] = useState(
    Number(f.consultation_credit) > 0 ? f.consultation_credit : (hints.consultation_credit ?? ""),
  );
  const fee = f.registrar_fee_amount !== null ? null : hints.registrar_fee;
  const [feeAmount, setFeeAmount] = useState(f.registrar_fee_amount ?? fee?.amount ?? "");
  const [feeCurrency, setFeeCurrency] = useState(
    f.registrar_fee_currency ?? fee?.currency ?? currencies.find((c) => c !== base) ?? base,
  );
  const [feeRate, setFeeRate] = useState(f.registrar_fee_rate ?? fee?.rate ?? "");
  const [terms, setTerms] = useState<Terms | "">(f.partner_terms ?? hints.partner_terms ?? "");
  const [percent, setPercent] = useState(f.partner_percent ?? hints.partner_percent ?? "");
  const [fixed, setFixed] = useState(f.partner_fixed ?? hints.partner_fixed ?? "");
  const [paidOn, setPaidOn] = useState(f.partner_paid_on ?? "");
  const today = todayIso();
  const rate = useBankRate(
    feeCurrency,
    today,
    base,
    setFeeRate,
    f.registrar_fee_currency && f.registrar_fee_rate
      ? {
          currency: f.registrar_fee_currency,
          day: today,
          rate: f.registrar_fee_rate,
        }
      : null,
  );
  const save = useSave(f.case_id, onDone, () =>
    apiPatch<Finance>(`/cases/${f.case_id}/finance`, {
      price: price ? toDecimal(price) : null,
      prepayment: prepayment ? toDecimal(prepayment) : null,
      consultation_credit: credit ? toDecimal(credit) : null,
      ...(feeAmount
        ? {
            registrar_fee_amount: toDecimal(feeAmount),
            registrar_fee_currency: feeCurrency,
            ...(feeCurrency !== base && feeRate ? { registrar_fee_rate: toDecimal(feeRate) } : {}),
          }
        : { registrar_fee_amount: null }),
      partner_terms: terms || null,
      partner_percent: terms === "percent" && percent ? toDecimal(percent) : null,
      partner_fixed: terms === "fixed" && fixed ? toDecimal(fixed) : null,
      partner_paid_on: (terms === "percent" || terms === "fixed") && paidOn ? paidOn : null,
    }),
  );

  return (
    <form
      className="form-card finance-form"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="form-grid">
        <MoneyField
          label={t("finance.priceField", { currency: base })}
          value={price}
          onChange={setPrice}
        />
        <MoneyField
          label={t("finance.prepaymentField", { currency: base })}
          value={prepayment}
          onChange={setPrepayment}
        />
        <MoneyField
          label={t("finance.consultationField", { currency: base })}
          value={credit}
          onChange={setCredit}
        />
      </div>
      <fieldset className="module-block">
        <legend>{t("finance.talonField")}</legend>
        <div className="form-grid">
          <MoneyField label={t("finance.amount")} value={feeAmount} onChange={setFeeAmount} />
          <label className="field">
            <span>{t("finance.currency")}</span>
            <select value={feeCurrency} onChange={(e) => setFeeCurrency(e.target.value)}>
              {currencies.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          {feeCurrency !== base && (
            <RateField value={feeRate} onChange={setFeeRate} missing={rate.isError} base={base} />
          )}
        </div>
      </fieldset>
      <fieldset className="module-block">
        <legend>{t("finance.partner")}</legend>
        <div className="form-grid">
          <label className="field">
            <span>{t("finance.partnerTerms")}</span>
            <select value={terms} onChange={(e) => setTerms(e.target.value as Terms | "")}>
              <option value="">—</option>
              {TERMS.map((value) => (
                <option key={value} value={value}>
                  {t(`dict.terms.${value}`)}
                </option>
              ))}
            </select>
          </label>
          {terms === "percent" && (
            <MoneyField label={t("finance.percent")} value={percent} onChange={setPercent} />
          )}
          {terms === "fixed" && (
            <MoneyField
              label={t("finance.fixedField", { currency: base })}
              value={fixed}
              onChange={setFixed}
            />
          )}
          {(terms === "percent" || terms === "fixed") && (
            <label className="field">
              <span>{t("finance.partnerPaidOn")}</span>
              <input type="date" value={paidOn} onChange={(e) => setPaidOn(e.target.value)} />
            </label>
          )}
        </div>
      </fieldset>
      {save.isError && <ErrorAlert error={save.error} />}
      <FormButtons pending={save.isPending} onCancel={onDone} />
    </form>
  );
}

function MoneyField({
  label,
  value,
  onChange,
  required = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        inputMode="decimal"
        required={required}
        pattern="[0-9 ]*([.,][0-9]{1,2})?"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function RateField({
  value,
  onChange,
  missing,
  base,
}: {
  value: string;
  onChange: (value: string) => void;
  missing: boolean;
  base: string;
}) {
  const { t } = useTranslation();
  return (
    <label className="field">
      <span>{t("finance.rate", { base })}</span>
      <input
        inputMode="decimal"
        required
        pattern="[0-9]*([.,][0-9]{1,6})?"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <small className="muted">{t(missing ? "finance.rateMissing" : "finance.rateHint")}</small>
    </label>
  );
}

/** The amount, its currency and the rate of the day: the base amount is shown as typed. */
function MoneyFields({
  base,
  day,
  amount,
  setAmount,
  currency,
  setCurrency,
  rate,
  setRate,
  saved,
}: {
  base: string;
  day: string;
  amount: string;
  setAmount: (value: string) => void;
  currency: string;
  setCurrency: (value: string) => void;
  rate: string;
  setRate: (value: string) => void;
  saved: Saved;
}) {
  const { t } = useTranslation();
  const money = useMoney();
  const currencies = usePaymentCurrencies(base);
  const bank = useBankRate(currency, day, base, setRate, saved);
  const total = Number(toDecimal(amount)) * (currency === base ? 1 : Number(toDecimal(rate)));
  return (
    <>
      <MoneyField label={t("finance.amount")} value={amount} onChange={setAmount} required />
      <label className="field">
        <span>{t("finance.currency")}</span>
        <select value={currency} onChange={(e) => setCurrency(e.target.value)}>
          {currencies.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        {currency !== base && Number.isFinite(total) && total > 0 && (
          <small className="muted">
            {t("finance.inBase", {
              amount: money(String(Math.round(total * 100) / 100)),
              base,
            })}
          </small>
        )}
      </label>
      {currency !== base && (
        <RateField value={rate} onChange={setRate} missing={bank.isError} base={base} />
      )}
    </>
  );
}

function PaymentForm({
  finance: f,
  payment,
  onDone,
}: {
  finance: Finance;
  payment: Payment | null;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const base = f.base_currency;
  const [paidOn, setPaidOn] = useState(payment?.paid_on ?? todayIso());
  const [amount, setAmount] = useState(payment?.amount ?? "");
  const [currency, setCurrency] = useState(payment?.currency ?? base);
  const [rate, setRate] = useState(payment && payment.currency !== base ? payment.rate : "");
  const [official, setOfficial] = useState(payment?.official ?? false);
  const [note, setNote] = useState(payment?.note ?? "");
  const save = useSave(f.case_id, onDone, () => {
    const body = {
      paid_on: paidOn,
      amount: toDecimal(amount),
      currency,
      ...(currency !== base && rate ? { rate: toDecimal(rate) } : {}),
      official,
      note: note.trim() || null,
    };
    return payment
      ? apiPatch<Finance>(`/payments/${payment.id}`, body)
      : apiPost<Finance>(`/cases/${f.case_id}/payments`, body);
  });
  return (
    <form
      className="form-card finance-form"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="form-grid">
        <label className="field">
          <span>{t("finance.paidOnField")}</span>
          <input type="date" required value={paidOn} onChange={(e) => setPaidOn(e.target.value)} />
        </label>
        <MoneyFields
          base={base}
          day={paidOn}
          amount={amount}
          setAmount={setAmount}
          currency={currency}
          setCurrency={setCurrency}
          rate={rate}
          setRate={setRate}
          saved={
            payment && {
              currency: payment.currency,
              day: payment.paid_on,
              rate: payment.rate,
            }
          }
        />
        <label className="field">
          <span>{t("finance.note")}</span>
          <input maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={official} onChange={(e) => setOfficial(e.target.checked)} />
        <span>{t("finance.officialLabel")}</span>
      </label>
      {save.isError && <SaveError error={save.error} />}
      <FormButtons pending={save.isPending} onCancel={onDone} />
    </form>
  );
}

function ExpenseForm({
  finance: f,
  expense,
  onDone,
}: {
  finance: Finance;
  expense: Expense | null;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const base = f.base_currency;
  const [spentOn, setSpentOn] = useState(expense?.spent_on ?? todayIso());
  const [description, setDescription] = useState(expense?.description ?? "");
  const [amount, setAmount] = useState(expense?.amount ?? "");
  const [currency, setCurrency] = useState(expense?.currency ?? base);
  const [rate, setRate] = useState(expense && expense.currency !== base ? expense.rate : "");
  const save = useSave(f.case_id, onDone, () => {
    const body = {
      spent_on: spentOn,
      description: description.trim(),
      amount: toDecimal(amount),
      currency,
      ...(currency !== base && rate ? { rate: toDecimal(rate) } : {}),
    };
    return expense
      ? apiPatch<Finance>(`/expenses/${expense.id}`, body)
      : apiPost<Finance>(`/cases/${f.case_id}/expenses`, body);
  });
  return (
    <form
      className="form-card finance-form"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <p className="muted small">{t("finance.expenseHint")}</p>
      <div className="form-grid">
        <label className="field">
          <span>{t("finance.spentOn")}</span>
          <input
            type="date"
            required
            value={spentOn}
            onChange={(e) => setSpentOn(e.target.value)}
          />
        </label>
        <label className="field">
          <span>{t("finance.description")}</span>
          <input
            required
            maxLength={200}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <MoneyFields
          base={base}
          day={spentOn}
          amount={amount}
          setAmount={setAmount}
          currency={currency}
          setCurrency={setCurrency}
          rate={rate}
          setRate={setRate}
          saved={
            expense && {
              currency: expense.currency,
              day: expense.spent_on,
              rate: expense.rate,
            }
          }
        />
      </div>
      {save.isError && <SaveError error={save.error} />}
      <FormButtons pending={save.isPending} onCancel={onDone} />
    </form>
  );
}

/** «Курса нет» asks for the rate by hand; other errors as usual. */
function SaveError({ error }: { error: unknown }) {
  const { t } = useTranslation();
  if (errorCode(error) === "rate_unavailable") {
    return (
      <div className="alert alert-warn" role="alert">
        {t("errors.rate_unavailable")}
      </div>
    );
  }
  return <ErrorAlert error={error} />;
}

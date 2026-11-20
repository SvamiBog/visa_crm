import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { apiDelete, apiGet, apiPatch, apiPost, errorCode } from "../../api";
import { Field, useFieldErrors, type FieldErrors } from "../../components/Field";
import { formatDate, type VisaCase } from "../../contacts";
import { deferDelete, useDeleting } from "../../undo";
import { ErrorAlert, todayIso } from "./ui";

// Case finances (backlog 1.9): the price and the balance in the case's own currency (pilot
// fix 4), the talon, payout, tax, expenses and profit in the specialist's base currency;
// payments and expenses in other currencies keep the site's National Bank rate. The API
// counts the balance, the payout, the tax and the profit (app/core/finance.py).

const TERMS = ["percent", "fixed", "none", "unspecified"] as const;
type Terms = (typeof TERMS)[number];

// The fields of each form that show the API's validation under them.
const FINANCE_FIELDS = [
  "currency",
  "rate",
  "price",
  "prepayment",
  "consultation_credit",
  "registrar_fee_amount",
  "registrar_fee_on",
  "registrar_fee_rate",
  "partner_percent",
  "partner_fixed",
  "partner_paid_on",
] as const;
const PAYMENT_FIELDS = ["paid_on", "amount", "rate", "note"] as const;
const EXPENSE_FIELDS = ["spent_on", "description", "amount", "rate"] as const;

type Payment = {
  id: string;
  paid_on: string;
  amount: string;
  currency: string;
  rate: string;
  amount_base: string;
  official: boolean;
  note: string | null;
  /** The payment in the case's currency, set against its price. */
  amount_case: string;
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
  /** The price's currency and its rate: base units for one unit. */
  currency: string;
  rate: string;
  price: string | null;
  prepayment: string | null;
  consultation_credit: string;
  registrar_fee_amount: string | null;
  registrar_fee_currency: string | null;
  registrar_fee_rate: string | null;
  registrar_fee_base: string | null;
  registrar_fee_on: string | null;
  registrar_fee_settled: boolean;
  partner_terms: Terms | null;
  partner_percent: string | null;
  partner_fixed: string | null;
  partner_paid_on: string | null;
  payments: Payment[];
  expenses: Expense[];
  paid: string;
  official: string;
  balance: string | null;
  price_base: string | null;
  partner_payout: string | null;
  /** Null while the workspace has no tax rate. */
  tax: string | null;
  expenses_total: string;
  profit: string | null;
  suggestions: {
    prepayment: string | null;
    registrar_fee: {
      amount: string;
      currency: string;
      on: string;
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

type Saved = { currency: string; rate: string } | null;

/** The site's latest National Bank rate of a currency, for a form to fill in; the same for
 * a payment of any day, and the user may type another. Empty for the base currency or when
 * the site has none yet. The rate already saved for this currency (maybe corrected by
 * hand) comes back instead. */
function useBankRate(
  currency: string,
  base: string,
  onRate: (rate: string) => void,
  saved: Saved,
) {
  const rate = useQuery({
    queryKey: ["rates", currency],
    queryFn: () => apiGet<{ rate: string }>(`/rates?currency=${currency}`),
    enabled: currency !== base,
    retry: false,
  });
  useEffect(() => {
    if (currency === base) onRate("");
    else if (saved && saved.currency === currency) onRate(saved.rate);
    // Empty while the rate is on its way, so a form cannot be saved with another one.
    else onRate(rate.data?.rate ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currency, base, rate.data]);
  return rate;
}

export function CaseFinance({ visaCase: c }: { visaCase: VisaCase }) {
  const { t, i18n } = useTranslation();
  const deleting = useDeleting();
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
  const cur = f.currency;
  // An amount with its currency: the price's and the base one may differ.
  const inCur = (value: string | null, currency: string) =>
    value === null ? "—" : `${money(value)} ${currency}`;
  // Without a price there is no balance or profit: ask for the price, never show a loss.
  const noPrice = <span className="muted">{t("finance.setPrice")}</span>;
  const close = () => {
    setEditing(null);
    setPayment(null);
    setExpense(null);
  };

  return (
    <section className="finance">
      <div className="card-head">
        <h4 className="h3">{t("finance.heading")}</h4>
        {editing === null && (
          <span className="actions">
            {/* A payment is the frequent one; the price and terms change rarely. */}
            <button type="button" className="btn btn-sm btn-p" onClick={() => setEditing("payment")}>
              {t("finance.addPayment")}
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setEditing("expense")}>
              {t("finance.addExpense")}
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setEditing("finance")}>
              {t("reference.edit")}
            </button>
          </span>
        )}
      </div>

      {editing === "finance" && <FinanceForm finance={f} onDone={close} />}
      {editing === "payment" && <PaymentForm finance={f} payment={payment} onDone={close} />}
      {editing === "expense" && <ExpenseForm finance={f} expense={expense} onDone={close} />}

      {f.price === null &&
      f.payments.length === 0 &&
      f.expenses.length === 0 &&
      f.registrar_fee_base === null ? (
        <p className="muted small">{t("finance.empty")}</p>
      ) : (
        <table className="tbl money-table">
          <tbody>
            <MoneyRow
              label={
                <>
                  {t("finance.price")}
                  {cur !== base && f.price_base !== null && (
                    <span className="muted">
                      {" · "}
                      {t("finance.priceInBase", {
                        amount: inCur(f.price_base, base),
                        rate: rateText(f.rate),
                      })}
                    </span>
                  )}
                </>
              }
              value={inCur(f.price, cur)}
            />
            {Number(f.consultation_credit) > 0 && (
              <MoneyRow
                label={t("finance.consultation")}
                value={inCur(f.consultation_credit, cur)}
              />
            )}
            {f.payments.map((p) => (
              <MoneyRow
                key={p.id}
                label={
                  <>
                    <PaymentLabel payment={p} base={cur} />
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
                value={inCur(p.amount_case, cur)}
                deleting={deleting.has(`/payments/${p.id}`)}
              />
            ))}
            <MoneyRow
              strong
              label={t("finance.balance")}
              value={f.price === null ? noPrice : inCur(f.balance, cur)}
            />
            {f.prepayment !== null && (
              <MoneyRow
                muted
                label={t("finance.prepaymentAgreed")}
                value={inCur(f.prepayment, cur)}
              />
            )}
            {f.registrar_fee_base !== null && (
              <MoneyRow
                label={
                  <>
                    {f.registrar_fee_currency === base
                      ? t("finance.talon")
                      : t("finance.talonForeign", {
                          amount: money(f.registrar_fee_amount),
                          currency: f.registrar_fee_currency,
                          rate: rateText(f.registrar_fee_rate),
                        })}
                    {f.registrar_fee_on && (
                      <span className="muted">
                        {" "}
                        · {formatDate(f.registrar_fee_on, i18n.language)}
                      </span>
                    )}
                    {f.registrar_fee_settled && (
                      <span className="muted"> · {t("finance.talonSettled")}</span>
                    )}
                  </>
                }
                value={inCur(f.registrar_fee_base, base)}
              />
            )}
            {f.partner_terms && f.partner_terms !== "unspecified" && (
              <MoneyRow
                label={<PartnerLabel finance={f} />}
                value={inCur(f.partner_payout, base)}
              />
            )}
            {f.tax === null ? (
              <MoneyRow
                label={
                  <>
                    {t("finance.taxNotSet")}
                    <br />
                    <small className="muted">{t("finance.taxNotSetHint")}</small>
                  </>
                }
                value="—"
              />
            ) : (
              <MoneyRow
                label={t("finance.tax", {
                  rate: money(f.tax_rate),
                  official: inCur(f.official, base),
                })}
                value={inCur(f.tax, base)}
              />
            )}
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
                value={inCur(e.amount_base, base)}
                deleting={deleting.has(`/expenses/${e.id}`)}
              />
            ))}
            <MoneyRow
              strong
              label={t("finance.profit")}
              value={f.price === null ? noPrice : inCur(f.profit, base)}
            />
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
  deleting = false,
}: {
  label: ReactNode;
  value: ReactNode;
  strong?: boolean;
  muted?: boolean;
  /** Waits for its deletion: struck through until it goes. */
  deleting?: boolean;
}) {
  return (
    <tr
      className={
        deleting ? "deleting" : strong ? "money-total" : muted ? "muted" : undefined
      }
    >
      <td>{label}</td>
      <td className="mono money-cell">{value}</td>
    </tr>
  );
}

/** `base`: the currency the row's value is in (the case's); a payment in another one shows
 * its amount and rate. */
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
  const deleting = useDeleting();
  const queryClient = useQueryClient();
  const waiting = deleting.has(deleteUrl);
  const remove = useMutation({
    mutationFn: () => apiDelete<Finance>(deleteUrl),
    onSuccess: (data) => {
      queryClient.setQueryData(financeKey(caseId), data);
      refreshBalances(queryClient);
    },
  });
  return (
    <span className="finance-actions">
      <button type="button" className="link-button" onClick={onEdit} disabled={waiting}>
        {t("reference.edit")}
      </button>
      <button
        type="button"
        className="link-button"
        disabled={remove.isPending || waiting}
        onClick={() =>
          deferDelete({
            key: deleteUrl,
            message: t("undo.recordDeleted"),
            commit: () => remove.mutateAsync(),
          })
        }
      >
        {t("vault.delete")}
      </button>
    </span>
  );
}

/** A payment changes the balance: its reminders and the home screen's debt. */
function refreshBalances(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: ["reminders"] });
  void queryClient.invalidateQueries({ queryKey: ["home"] });
}

/** Saves a form; the API answers with the case's finance counted again. */
function useSave(caseId: string, onDone: () => void, request: () => Promise<Finance>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: request,
    onSuccess: (data: Finance) => {
      queryClient.setQueryData(financeKey(caseId), data);
      refreshBalances(queryClient);
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
  // The price's currency (pilot fix 4) and its rate to the base one.
  const [currency, setCurrency] = useState(f.currency);
  // The case keeps its currency even if the specialist no longer accepts it.
  const priceCurrencies = currencies.includes(f.currency) ? currencies : [...currencies, f.currency];
  const [priceRate, setPriceRate] = useState(f.currency !== base ? f.rate : "");
  const priceBank = useBankRate(currency, base, setPriceRate, {
    currency: f.currency,
    rate: f.rate,
  });
  // Empty fields start from the lists and the consultation, which are in the base currency.
  const hinted = f.currency === base;
  const [price, setPrice] = useState(f.price ?? "");
  const [prepayment, setPrepayment] = useState(
    f.prepayment ?? (hinted ? hints.prepayment : null) ?? "",
  );
  const [credit, setCredit] = useState(
    Number(f.consultation_credit) > 0
      ? f.consultation_credit
      : ((hinted ? hints.consultation_credit : null) ?? ""),
  );
  const fee = f.registrar_fee_amount !== null ? null : hints.registrar_fee;
  const [feeAmount, setFeeAmount] = useState(f.registrar_fee_amount ?? fee?.amount ?? "");
  const [feeCurrency, setFeeCurrency] = useState(
    f.registrar_fee_currency ?? fee?.currency ?? currencies.find((c) => c !== base) ?? base,
  );
  const [feeRate, setFeeRate] = useState(f.registrar_fee_rate ?? fee?.rate ?? "");
  // The day the talon is charged to the registrar: when the client was queued.
  const [feeOn, setFeeOn] = useState(f.registrar_fee_on ?? fee?.on ?? todayIso());
  // Paid to the registrar before the CRM (a talon from the tables): not in the settlements.
  const [settled, setSettled] = useState(f.registrar_fee_settled);
  const [terms, setTerms] = useState<Terms | "">(f.partner_terms ?? hints.partner_terms ?? "");
  const [percent, setPercent] = useState(f.partner_percent ?? hints.partner_percent ?? "");
  const [fixed, setFixed] = useState(f.partner_fixed ?? hints.partner_fixed ?? "");
  const [paidOn, setPaidOn] = useState(f.partner_paid_on ?? "");
  const rate = useBankRate(
    feeCurrency,
    base,
    setFeeRate,
    f.registrar_fee_currency && f.registrar_fee_rate
      ? { currency: f.registrar_fee_currency, rate: f.registrar_fee_rate }
      : null,
  );
  const save = useSave(f.case_id, onDone, () =>
    apiPatch<Finance>(`/cases/${f.case_id}/finance`, {
      currency,
      ...(currency !== base && priceRate ? { rate: toDecimal(priceRate) } : {}),
      price: price ? toDecimal(price) : null,
      prepayment: prepayment ? toDecimal(prepayment) : null,
      consultation_credit: credit ? toDecimal(credit) : null,
      ...(feeAmount
        ? {
            registrar_fee_amount: toDecimal(feeAmount),
            registrar_fee_currency: feeCurrency,
            registrar_fee_on: feeOn,
            registrar_fee_settled: settled,
            ...(feeCurrency !== base && feeRate ? { registrar_fee_rate: toDecimal(feeRate) } : {}),
          }
        : { registrar_fee_amount: null }),
      partner_terms: terms || null,
      partner_percent: terms === "percent" && percent ? toDecimal(percent) : null,
      partner_fixed: terms === "fixed" && fixed ? toDecimal(fixed) : null,
      partner_paid_on: (terms === "percent" || terms === "fixed") && paidOn ? paidOn : null,
    }),
  );
  const errors = useFieldErrors(save.error, {
    codes: {
      currency_not_accepted: "currency",
      ...(currency !== base ? { rate_unavailable: "rate" } : {}),
      registrar_fee_incomplete: "registrar_fee_amount",
      partner_terms_incomplete: terms === "fixed" ? "partner_fixed" : "partner_percent",
      ...(feeCurrency !== base ? { rate_unavailable: "registrar_fee_rate" } : {}),
    },
    fields: FINANCE_FIELDS,
  });

  // The suggestions are in the base currency: an untouched one does not carry over to
  // another currency.
  const switchCurrency = (next: string) => {
    if (hinted && next !== base) {
      if (f.prepayment === null && prepayment === hints.prepayment) setPrepayment("");
      if (Number(f.consultation_credit) === 0 && credit === hints.consultation_credit) {
        setCredit("");
      }
    }
    setCurrency(next);
  };

  return (
    <form
      ref={errors.form}
      className="form-card finance-form"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="form-grid">
        <Field
          label={t("finance.priceCurrency")}
          hint={t("finance.priceCurrencyHint", { base })}
          error={errors.at("currency")}
        >
          {(control) => (
            <select {...control} value={currency} onChange={(e) => switchCurrency(e.target.value)}>
              {priceCurrencies.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          )}
        </Field>
        {currency !== base && (
          <RateField
            value={priceRate}
            onChange={setPriceRate}
            missing={priceBank.isError}
            base={base}
            error={errors.at("rate")}
          />
        )}
        <MoneyField
          label={t("finance.priceField", { currency })}
          value={price}
          onChange={setPrice}
          error={errors.at("price")}
        />
        <MoneyField
          label={t("finance.prepaymentField", { currency })}
          value={prepayment}
          onChange={setPrepayment}
          error={errors.at("prepayment")}
        />
        <MoneyField
          label={t("finance.consultationField", { currency })}
          value={credit}
          onChange={setCredit}
          error={errors.at("consultation_credit")}
        />
      </div>
      <fieldset className="module-block">
        <legend>{t("finance.talonField")}</legend>
        <div className="form-grid">
          <MoneyField
            label={t("finance.amount")}
            value={feeAmount}
            onChange={setFeeAmount}
            error={errors.at("registrar_fee_amount")}
          />
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
          <Field label={t("finance.talonOn")} error={errors.at("registrar_fee_on")}>
            {(control) => (
              <input
                {...control}
                type="date"
                required={Boolean(feeAmount)}
                value={feeOn}
                onChange={(e) => setFeeOn(e.target.value)}
              />
            )}
          </Field>
          {feeCurrency !== base && (
            <RateField
              value={feeRate}
              onChange={setFeeRate}
              missing={rate.isError}
              base={base}
              error={errors.at("registrar_fee_rate")}
            />
          )}
        </div>
        {feeAmount && (
          <label className="checkbox">
            <input
              type="checkbox"
              checked={settled}
              onChange={(e) => setSettled(e.target.checked)}
            />
            <span>{t("finance.talonSettledField")}</span>
          </label>
        )}
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
            <MoneyField
              label={t("finance.percent")}
              value={percent}
              onChange={setPercent}
              error={errors.at("partner_percent")}
            />
          )}
          {terms === "fixed" && (
            <MoneyField
              label={t("finance.fixedField", { currency: base })}
              value={fixed}
              onChange={setFixed}
              error={errors.at("partner_fixed")}
            />
          )}
          {(terms === "percent" || terms === "fixed") && (
            <Field label={t("finance.partnerPaidOn")} error={errors.at("partner_paid_on")}>
              {(control) => (
                <input
                  {...control}
                  type="date"
                  value={paidOn}
                  onChange={(e) => setPaidOn(e.target.value)}
                />
              )}
            </Field>
          )}
        </div>
      </fieldset>
      {errors.general ? <ErrorAlert error={errors.general} /> : null}
      <FormButtons pending={save.isPending} onCancel={onDone} />
    </form>
  );
}

function MoneyField({
  label,
  value,
  onChange,
  required = false,
  error,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  error?: ReactNode;
}) {
  return (
    <Field label={label} error={error}>
      {(control) => (
        <input
          {...control}
          inputMode="decimal"
          required={required}
          pattern="[0-9 ]*([.,][0-9]{1,2})?"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </Field>
  );
}

function RateField({
  value,
  onChange,
  missing,
  base,
  error,
}: {
  value: string;
  onChange: (value: string) => void;
  missing: boolean;
  base: string;
  error?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <Field
      label={t("finance.rate", { base })}
      hint={t(missing ? "finance.rateMissing" : "finance.rateHint")}
      error={error}
    >
      {(control) => (
        <input
          {...control}
          inputMode="decimal"
          required
          pattern="[0-9]*([.,][0-9]{1,6})?"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </Field>
  );
}

/** The amount, its currency and the rate: the base amount is shown as typed. */
function MoneyFields({
  base,
  amount,
  setAmount,
  currency,
  setCurrency,
  rate,
  setRate,
  saved,
  errors,
}: {
  base: string;
  amount: string;
  setAmount: (value: string) => void;
  currency: string;
  setCurrency: (value: string) => void;
  rate: string;
  setRate: (value: string) => void;
  saved: Saved;
  errors: FieldErrors;
}) {
  const { t } = useTranslation();
  const money = useMoney();
  const currencies = usePaymentCurrencies(base);
  const bank = useBankRate(currency, base, setRate, saved);
  const total = Number(toDecimal(amount)) * (currency === base ? 1 : Number(toDecimal(rate)));
  return (
    <>
      <MoneyField
        label={t("finance.amount")}
        value={amount}
        onChange={setAmount}
        required
        error={errors.at("amount")}
      />
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
        <RateField
          value={rate}
          onChange={setRate}
          missing={bank.isError}
          base={base}
          error={errors.at("rate")}
        />
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
  const errors = useFieldErrors(save.error, {
    codes: currency !== base ? { rate_unavailable: "rate" } : {},
    fields: PAYMENT_FIELDS,
  });
  return (
    <form
      ref={errors.form}
      className="form-card finance-form"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="form-grid">
        <Field label={t("finance.paidOnField")} error={errors.at("paid_on")}>
          {(control) => (
            <input
              {...control}
              type="date"
              required
              value={paidOn}
              onChange={(e) => setPaidOn(e.target.value)}
            />
          )}
        </Field>
        <MoneyFields
          base={base}
          amount={amount}
          setAmount={setAmount}
          currency={currency}
          setCurrency={setCurrency}
          rate={rate}
          setRate={setRate}
          saved={
            payment && {
              currency: payment.currency,
              rate: payment.rate,
            }
          }
          errors={errors}
        />
        <Field label={t("finance.note")} error={errors.at("note")}>
          {(control) => (
            <input
              {...control}
              maxLength={200}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          )}
        </Field>
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={official} onChange={(e) => setOfficial(e.target.checked)} />
        <span>{t("finance.officialLabel")}</span>
      </label>
      {errors.general ? <SaveError error={errors.general} /> : null}
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
  const errors = useFieldErrors(save.error, {
    codes: currency !== base ? { rate_unavailable: "rate" } : {},
    fields: EXPENSE_FIELDS,
  });
  return (
    <form
      ref={errors.form}
      className="form-card finance-form"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <p className="muted small">{t("finance.expenseHint")}</p>
      <div className="form-grid">
        <Field label={t("finance.spentOn")} error={errors.at("spent_on")}>
          {(control) => (
            <input
              {...control}
              type="date"
              required
              value={spentOn}
              onChange={(e) => setSpentOn(e.target.value)}
            />
          )}
        </Field>
        <Field label={t("finance.description")} error={errors.at("description")}>
          {(control) => (
            <input
              {...control}
              required
              maxLength={200}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          )}
        </Field>
        <MoneyFields
          base={base}
          amount={amount}
          setAmount={setAmount}
          currency={currency}
          setCurrency={setCurrency}
          rate={rate}
          setRate={setRate}
          saved={
            expense && {
              currency: expense.currency,
              rate: expense.rate,
            }
          }
          errors={errors}
        />
      </div>
      {errors.general ? <SaveError error={errors.general} /> : null}
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

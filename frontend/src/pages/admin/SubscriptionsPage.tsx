import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { apiDelete, apiPatch, apiPost, apiPut } from "../../api";
import { Field, useFieldErrors } from "../../components/Field";
import { PaymentsTable, SubscriptionChip } from "../../components/Subscription";
import { formatDate } from "../../contacts";
import {
  CURRENCIES,
  SUBSCRIPTIONS_KEY,
  TARIFFS_KEY,
  useSubscriptionOf,
  useSubscriptions,
  useTariffs,
  type Currency,
  type Subscription,
  type SubscriptionPayment,
  type Tariff,
  type TariffBrief,
} from "../../subscriptions";
import { useMoney } from "../clients/Finance";
import { ErrorAlert, todayIso } from "../clients/ui";

// «Подписки» (roadmap 3.8): every specialist's subscription, the one opened with its
// tariff, dates and payments, and the tariffs. Payments arrive outside the system; the
// admin marks them here.

const refresh = (queryClient: QueryClient) => {
  void queryClient.invalidateQueries({ queryKey: SUBSCRIPTIONS_KEY });
  void queryClient.invalidateQueries({ queryKey: TARIFFS_KEY });
};

export function SubscriptionsPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const subscriptions = useSubscriptions();
  const opened = params.get("specialist");
  const name = subscriptions.data?.find((s) => s.tenant_id === opened)?.name ?? "…";

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("subscription.pageTitle")}</h1>
          <div className="muted">{t("subscription.pageSubtitle")}</div>
        </div>
      </header>

      {opened && (
        <SpecialistSubscription
          key={opened}
          tenantId={opened}
          name={name}
          onClose={() => setParams({})}
        />
      )}

      <section className="card">
        <h2 className="h2">{t("subscription.specialists")}</h2>
        {subscriptions.isPending && <p className="muted">{t("common.loading")}</p>}
        {subscriptions.isError && <ErrorAlert error={subscriptions.error} />}
        {subscriptions.data?.length === 0 && <p className="muted">{t("admin.empty")}</p>}
        {subscriptions.data && subscriptions.data.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("subscription.colSpecialist")}</th>
                  <th>{t("subscription.colTariff")}</th>
                  <th>{t("subscription.colStatus")}</th>
                  <th>{t("subscription.colNextDue")}</th>
                  <th>{t("subscription.colLastPaid")}</th>
                  <th aria-label={t("admin.colActions")} />
                </tr>
              </thead>
              <tbody>
                {subscriptions.data.map((s) => (
                  <tr key={s.tenant_id}>
                    <td>
                      <div>{s.name}</div>
                      <div className="muted small">
                        {s.email}
                        {!s.is_active && ` · ${t("subscription.blocked")}`}
                      </div>
                    </td>
                    <td>{s.tariff ?? "—"}</td>
                    <td>
                      <SubscriptionChip state={s} />
                    </td>
                    <td className="nowrap">
                      <DateText value={s.next_due_on} />
                    </td>
                    <td className="nowrap muted">
                      <DateText value={s.last_paid_on} />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => setParams({ specialist: s.tenant_id })}
                      >
                        {t("subscription.open")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <Tariffs />
    </div>
  );
}

function DateText({ value }: { value: string | null }) {
  const { i18n } = useTranslation();
  return <>{formatDate(value, i18n.language) || "—"}</>;
}

/** One specialist: the tariff and dates, a payment to mark and the history. */
function SpecialistSubscription({
  tenantId,
  name,
  onClose,
}: {
  tenantId: string;
  name: string;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const subscription = useSubscriptionOf(tenantId);
  const remove = useMutation({
    mutationFn: (payment: SubscriptionPayment) =>
      apiDelete<void>(`/admin/subscription-payments/${payment.id}`),
    onSuccess: () => refresh(queryClient),
  });
  const s = subscription.data;

  return (
    <section className="card form-card" aria-label={t("subscription.of", { name })}>
      <div className="card-head">
        <h2 className="h2">{t("subscription.of", { name })}</h2>
        <div className="actions">
          <button type="button" className="btn btn-sm" onClick={onClose}>
            {t("subscription.close")}
          </button>
        </div>
      </div>
      {subscription.isPending && <p className="muted">{t("common.loading")}</p>}
      {subscription.isError && <ErrorAlert error={subscription.error} />}
      {s && (
        <>
          <p>
            <SubscriptionChip state={s} />
            {s.next_due_on && (
              <span className="muted">
                {" "}
                {t("subscription.colNextDue")}: {formatDate(s.next_due_on, i18n.language)}
              </span>
            )}
          </p>
          <SubscriptionForm
            key={`${s.tariff?.id}-${s.starts_on}-${s.trial_until}`}
            tenantId={tenantId}
            subscription={s}
          />
          {s.tariff && <PaymentForm tenantId={tenantId} tariff={s.tariff} />}
          <h3 className="h3">{t("subscription.payments")}</h3>
          <PaymentsTable
            payments={s.payments}
            deleting={remove.isPending}
            onDelete={(payment) => {
              const date = formatDate(payment.paid_on, i18n.language);
              if (window.confirm(t("subscription.confirmDeletePayment", { date }))) {
                remove.mutate(payment);
              }
            }}
          />
          {remove.isError && <ErrorAlert error={remove.error} />}
        </>
      )}
    </section>
  );
}

function DateField({
  label,
  hint,
  value,
  onChange,
  disabled,
  error,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  error?: string;
}) {
  return (
    <Field label={label} hint={hint} error={error}>
      {(control) => (
        <input
          {...control}
          type="date"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </Field>
  );
}

function SubscriptionForm({
  tenantId,
  subscription,
}: {
  tenantId: string;
  subscription: Subscription;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const tariffs = useTariffs();
  const money = useMoney();
  const current = subscription.tariff?.id ?? "";
  const [tariffId, setTariffId] = useState(current);
  const [startsOn, setStartsOn] = useState(subscription.starts_on ?? "");
  const [trialUntil, setTrialUntil] = useState(subscription.trial_until ?? "");
  // An inactive tariff stays for the one already on it, but is not offered anew.
  const choices = (tariffs.data ?? []).filter((x) => x.is_active || x.id === current);

  const save = useMutation({
    mutationFn: () =>
      apiPut<Subscription>(`/admin/specialists/${tenantId}/subscription`, {
        tariff_id: tariffId || null,
        starts_on: (tariffId && startsOn) || null,
        trial_until: (tariffId && trialUntil) || null,
      }),
    onSuccess: () => refresh(queryClient),
  });
  const errors = useFieldErrors(save.error, {
    codes: {
      unknown_tariff: "tariff_id",
      tariff_inactive: "tariff_id",
      trial_before_start: "trial_until",
    },
    fields: ["tariff_id", "starts_on", "trial_until"],
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form ref={errors.form} onSubmit={submit}>
      <div className="form-grid">
        <Field label={t("subscription.tariff")} error={errors.at("tariff_id")}>
          {(control) => (
            <select {...control} value={tariffId} onChange={(e) => setTariffId(e.target.value)}>
              <option value="">{t("subscription.noTariff")}</option>
              {choices.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name} ·{" "}
                  {t("subscription.perMonth", { amount: `${money(x.price)} ${x.currency}` })}
                </option>
              ))}
            </select>
          )}
        </Field>
        <DateField
          label={t("subscription.startsOn")}
          hint={t("subscription.startsOnHint")}
          value={startsOn}
          onChange={setStartsOn}
          disabled={!tariffId}
          error={errors.at("starts_on")}
        />
        <DateField
          label={t("subscription.trialUntil")}
          hint={t("subscription.trialHint")}
          value={trialUntil}
          onChange={setTrialUntil}
          disabled={!tariffId}
          error={errors.at("trial_until")}
        />
      </div>
      {errors.general ? <ErrorAlert error={errors.general} /> : null}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={save.isPending}>
          {t("common.save")}
        </button>
      </div>
    </form>
  );
}

function PaymentForm({ tenantId, tariff }: { tenantId: string; tariff: TariffBrief }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const money = useMoney();
  const [paidOn, setPaidOn] = useState(todayIso);
  const [months, setMonths] = useState("1");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState<Currency>(tariff.currency);
  const [note, setNote] = useState("");

  const add = useMutation({
    mutationFn: () =>
      apiPost<Subscription>(`/admin/specialists/${tenantId}/subscription/payments`, {
        paid_on: paidOn,
        months: Number(months),
        // Left empty, the API takes the tariff's price for the months, in its currency.
        amount: amount || null,
        currency: amount ? currency : null,
        note: note.trim() || null,
      }),
    onSuccess: () => {
      setMonths("1");
      setAmount("");
      setNote("");
      refresh(queryClient);
    },
  });
  const errors = useFieldErrors(add.error, { fields: ["paid_on", "months", "amount", "note"] });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    add.mutate();
  };

  return (
    <form ref={errors.form} onSubmit={submit}>
      <h3 className="h3">{t("subscription.markPayment")}</h3>
      <div className="form-grid">
        <Field label={t("subscription.paidOn")} error={errors.at("paid_on")}>
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
        <Field label={t("subscription.months")} error={errors.at("months")}>
          {(control) => (
            <input
              {...control}
              type="number"
              required
              min={1}
              max={24}
              step={1}
              value={months}
              onChange={(e) => setMonths(e.target.value)}
            />
          )}
        </Field>
        <Field
          label={t("subscription.amount")}
          hint={t("subscription.amountHint", {
            amount: `${money(tariff.price)} ${tariff.currency}`,
          })}
          error={errors.at("amount")}
        >
          {(control) => (
            <input
              {...control}
              type="number"
              inputMode="decimal"
              min="0.01"
              step="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          )}
        </Field>
        <label className="field">
          <span>{t("subscription.currency")}</span>
          <select
            value={currency}
            disabled={!amount}
            onChange={(e) => setCurrency(e.target.value as Currency)}
          >
            {CURRENCIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <Field label={t("subscription.note")} error={errors.at("note")}>
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
      {errors.general ? <ErrorAlert error={errors.general} /> : null}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={add.isPending || !paidOn}>
          {t("subscription.markPayment")}
        </button>
      </div>
    </form>
  );
}

function Tariffs() {
  const { t } = useTranslation();
  const tariffs = useTariffs();
  const money = useMoney();
  const [editing, setEditing] = useState<Tariff | "new" | null>(null);

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="h2">{t("subscription.tariffs")}</h2>
        {editing === null && (
          <div className="actions">
            <button type="button" className="btn btn-sm" onClick={() => setEditing("new")}>
              {t("subscription.addTariff")}
            </button>
          </div>
        )}
      </div>
      <p className="muted small">{t("subscription.tariffsHint")}</p>
      {editing !== null && (
        <TariffForm
          key={editing === "new" ? "new" : editing.id}
          tariff={editing === "new" ? null : editing}
          onDone={() => setEditing(null)}
        />
      )}
      {tariffs.isPending && <p className="muted">{t("common.loading")}</p>}
      {tariffs.isError && <ErrorAlert error={tariffs.error} />}
      {tariffs.data?.length === 0 && <p className="muted">{t("subscription.noTariffs")}</p>}
      {tariffs.data && tariffs.data.length > 0 && (
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>{t("subscription.tariffName")}</th>
                <th>{t("subscription.price")}</th>
                <th>{t("subscription.onTariff")}</th>
                <th>{t("subscription.colStatus")}</th>
                <th aria-label={t("admin.colActions")} />
              </tr>
            </thead>
            <tbody>
              {tariffs.data.map((tariff) => (
                <tr key={tariff.id}>
                  <td>{tariff.name}</td>
                  <td className="nowrap">
                    {money(tariff.price)} {tariff.currency}
                  </td>
                  <td>{tariff.specialists}</td>
                  <td>
                    <span className={tariff.is_active ? "chip st-green" : "chip st-gray"}>
                      {t(
                        tariff.is_active
                          ? "subscription.tariffActive"
                          : "subscription.tariffInactive",
                      )}
                    </span>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => setEditing(tariff)}
                    >
                      {t("subscription.edit")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function TariffForm({ tariff, onDone }: { tariff: Tariff | null; onDone: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [name, setName] = useState(tariff?.name ?? "");
  const [price, setPrice] = useState(tariff ? String(Number(tariff.price)) : "");
  const [currency, setCurrency] = useState<Currency>(tariff?.currency ?? "BYN");
  const [active, setActive] = useState(tariff?.is_active ?? true);

  const save = useMutation({
    mutationFn: () =>
      tariff
        ? apiPatch<Tariff>(`/admin/tariffs/${tariff.id}`, {
            name,
            price,
            currency,
            is_active: active,
          })
        : apiPost<Tariff>("/admin/tariffs", { name, price, currency }),
    onSuccess: () => {
      refresh(queryClient);
      onDone();
    },
  });
  const errors = useFieldErrors(save.error, {
    codes: { tariff_name_taken: "name" },
    fields: ["name", "price"],
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <form ref={errors.form} className="form-card" onSubmit={submit}>
      <h3 className="h3">
        {tariff ? t("subscription.editTariff", { name: tariff.name }) : t("subscription.newTariff")}
      </h3>
      <div className="form-grid">
        <Field label={t("subscription.tariffName")} error={errors.at("name")}>
          {(control) => (
            <input
              {...control}
              required
              autoFocus
              maxLength={100}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>
        <Field label={t("subscription.price")} error={errors.at("price")}>
          {(control) => (
            <input
              {...control}
              type="number"
              required
              inputMode="decimal"
              min="0.01"
              step="0.01"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
            />
          )}
        </Field>
        <label className="field">
          <span>{t("subscription.currency")}</span>
          <select value={currency} onChange={(e) => setCurrency(e.target.value as Currency)}>
            {CURRENCIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
      </div>
      {tariff && (
        <>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={active}
              onChange={(e) => setActive(e.target.checked)}
            />
            {t("subscription.tariffActive")}
          </label>
          <p className="muted small">{t("subscription.tariffActiveHint")}</p>
        </>
      )}
      {errors.general ? <ErrorAlert error={errors.general} /> : null}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={save.isPending || !name || !price}>
          {t("common.save")}
        </button>
        <button type="button" className="btn" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { apiDelete, apiGet, apiPatch, apiPost } from "../../api";
import { formatDate, type CaseStage } from "../../contacts";
import { byId, useCountries, useLocalName } from "../../reference";
import { CaseChip } from "../clients/Cases";
import { useMoney } from "../clients/Finance";
import { ErrorAlert, todayIso } from "../clients/ui";

// Settlements with registrars (backlog 1.10): each registrar's balance in their own
// currency — the specialist's payments minus the talons of the clients they queued.
// A positive balance is an advance, a negative one a debt (app/core/registrar_ledger.py).

type Method = "cash" | "transfer";

type Summary = {
  id: string;
  name: string;
  kind: "company" | "individual";
  is_active: boolean;
  currency: string;
  low_balance: string | null;
  paid: string;
  charged: string;
  balance: string;
  talons: number;
  low: boolean;
  missing: number;
  last_payment_on: string | null;
};

type CaseRef = {
  case_id: string;
  case_number: number;
  contact_id: string;
  contact_name: string;
  destination_id: string;
};

type Line = {
  kind: "payment" | "talon";
  on: string;
  amount: string;
  balance: string;
  payment_id: string | null;
  method: Method | null;
  talons: number | null;
  note: string | null;
} & Partial<CaseRef>;

type Ledger = Summary & {
  lines: Line[];
  missing_talons: (CaseRef & {
    stage: CaseStage;
    queued_on: string | null;
    price: string | null;
    price_currency: string | null;
  })[];
  other_currency: (CaseRef & { on: string; amount: string; currency: string })[];
};

const ledgerKey = (id: string) => ["registrars", "ledger", id] as const;

/** Amounts are typed with a comma or a dot; the API takes a decimal string. */
function toDecimal(value: string): string {
  return value.trim().replace(/\s/g, "").replace(",", ".");
}

export function RegistrarsPage() {
  const { id } = useParams();
  return id ? <RegistrarLedger id={id} /> : <RegistrarList />;
}

function BalanceChips({ summary: s }: { summary: Summary }) {
  const { t } = useTranslation();
  const money = useMoney();
  const balance = Number(s.balance);
  const amount = money(String(Math.abs(balance)));
  return (
    <span className="chips-inline">
      {balance > 0 && (
        <span className="chip st-green">
          {t("registrars.advance", { amount, currency: s.currency })}
        </span>
      )}
      {balance < 0 && (
        <span className="chip st-red">
          {t("registrars.debt", { amount, currency: s.currency })}
        </span>
      )}
      {balance === 0 && <span className="chip st-gray">{t("registrars.settled")}</span>}
      {s.low && balance >= 0 && <span className="chip st-orange">{t("registrars.low")}</span>}
    </span>
  );
}

function RegistrarList() {
  const { t, i18n } = useTranslation();
  const list = useQuery({
    queryKey: ["registrars", "ledgers"],
    queryFn: () => apiGet<Summary[]>("/registrars/ledgers"),
  });

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("registrars.title")}</h1>
          <div className="muted">{t("registrars.subtitle")}</div>
        </div>
      </header>
      <section className="card">
        {list.isPending && <p className="muted">{t("common.loading")}</p>}
        {list.isError && <ErrorAlert error={list.error} />}
        {list.isSuccess && list.data.length === 0 && (
          <p className="muted">{t("registrars.none")}</p>
        )}
        {list.data && list.data.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("registrars.colRegistrar")}</th>
                  <th>{t("registrars.colBalance")}</th>
                  <th>{t("registrars.colTalons")}</th>
                  <th>{t("registrars.colMissing")}</th>
                  <th>{t("registrars.colLastPayment")}</th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((r) => (
                  <tr key={r.id} className={r.is_active ? undefined : "row-off"}>
                    <td>
                      <Link to={`/app/registrars/${r.id}`}>{r.name}</Link>
                      <div className="muted small">{t(`dict.kind.${r.kind}`)}</div>
                    </td>
                    <td>
                      <BalanceChips summary={r} />
                    </td>
                    <td className="mono">{r.talons}</td>
                    <td className={r.missing > 0 ? "warn-text" : "muted"}>{r.missing || "—"}</td>
                    <td className="nowrap muted">
                      {r.last_payment_on ? formatDate(r.last_payment_on, i18n.language) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function RegistrarLedger({ id }: { id: string }) {
  const { t, i18n } = useTranslation();
  const money = useMoney();
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const ledger = useQuery({
    queryKey: ledgerKey(id),
    queryFn: () => apiGet<Ledger>(`/registrars/${id}/ledger`),
  });
  const [editing, setEditing] = useState<Line | "new" | null>(null);
  const date = (value: string | null) => formatDate(value, i18n.language);
  const where = (ref: Partial<CaseRef>) =>
    [
      t("cases.number", { number: ref.case_number }),
      name.country(countryById.get(ref.destination_id ?? "")),
    ].join(" · ");

  const back = (
    <Link to="/app/registrars" className="small">
      ← {t("registrars.back")}
    </Link>
  );
  if (ledger.isPending) {
    return (
      <div className="page">
        {back}
        <p className="muted">{t("common.loading")}</p>
      </div>
    );
  }
  if (ledger.isError) {
    return (
      <div className="page">
        {back}
        <ErrorAlert error={ledger.error} />
      </div>
    );
  }
  const l = ledger.data;

  return (
    <div className="page">
      {back}
      <header className="page-header">
        <div>
          <h1>{l.name}</h1>
          <div className="muted">
            {t(`dict.kind.${l.kind}`)} · {t("registrars.currencyOf", { currency: l.currency })}
          </div>
        </div>
        {editing === null && (
          <button type="button" className="btn btn-p" onClick={() => setEditing("new")}>
            {t("registrars.addPayment")}
          </button>
        )}
      </header>

      <section className="card ledger-summary">
        <BalanceChips summary={l} />
        <div className="muted small">
          {t("registrars.paidCharged", {
            paid: money(l.paid),
            charged: money(l.charged),
            currency: l.currency,
            number: l.talons,
          })}
        </div>
        <div className="muted small">
          {l.low_balance !== null
            ? t("registrars.threshold", { amount: money(l.low_balance), currency: l.currency })
            : t("registrars.noThreshold")}
        </div>
      </section>

      {editing !== null && (
        <PaymentForm
          registrarId={l.id}
          currency={l.currency}
          payment={editing === "new" ? null : editing}
          onDone={() => setEditing(null)}
        />
      )}

      <section className="card">
        <h2 className="h2">{t("registrars.linesTitle")}</h2>
        {l.lines.length === 0 ? (
          <p className="muted">{t("registrars.noLines")}</p>
        ) : (
          <div className="table-wrap">
            <table className="tbl ledger-table">
              <thead>
                <tr>
                  <th>{t("registrars.colDate")}</th>
                  <th>{t("registrars.colEntry")}</th>
                  <th className="money-cell">
                    {t("registrars.colAmount", { currency: l.currency })}
                  </th>
                  <th className="money-cell">{t("registrars.colBalanceAfter")}</th>
                  <th aria-label={t("admin.colActions")} />
                </tr>
              </thead>
              <tbody>
                {l.lines.map((line) =>
                  line.kind === "payment" ? (
                    <tr key={line.payment_id}>
                      <td className="nowrap">{date(line.on)}</td>
                      <td>
                        {t("registrars.payment")} · {t(`registrars.method.${line.method}`)}
                        <div className="muted small">
                          {line.talons
                            ? t("registrars.forTalons", { number: line.talons })
                            : t("registrars.advancePayment")}
                          {line.note && ` · ${line.note}`}
                        </div>
                      </td>
                      <td className="mono money-cell amount-plus">+{money(line.amount)}</td>
                      <td className="mono money-cell">{money(line.balance)}</td>
                      <td>
                        <PaymentActions
                          registrarId={l.id}
                          paymentId={line.payment_id ?? ""}
                          onEdit={() => setEditing(line)}
                        />
                      </td>
                    </tr>
                  ) : (
                    <tr key={`talon-${line.case_id}`}>
                      <td className="nowrap">{date(line.on)}</td>
                      <td>
                        {t("registrars.talon")} ·{" "}
                        <Link to={`/app/clients/${line.contact_id}`}>{line.contact_name}</Link>
                        <div className="muted small">{where(line)}</div>
                      </td>
                      <td className="mono money-cell">−{money(line.amount)}</td>
                      <td className="mono money-cell">{money(line.balance)}</td>
                      <td />
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {l.missing_talons.length > 0 && <MissingTalons ledger={l} where={where} />}

      {l.other_currency.length > 0 && (
        <section className="card">
          <h2 className="h2">{t("registrars.otherCurrencyTitle")}</h2>
          <p className="muted small">
            {t("registrars.otherCurrencyHint", { currency: l.currency })}
          </p>
          <ul className="plain-list">
            {l.other_currency.map((o) => (
              <li key={o.case_id}>
                <Link to={`/app/clients/${o.contact_id}`}>{o.contact_name}</Link>{" "}
                <span className="muted small">
                  {where(o)} · {date(o.on)}
                </span>{" "}
                <span className="mono">
                  {money(o.amount)} {o.currency}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function MissingTalons({
  ledger: l,
  where,
}: {
  ledger: Ledger;
  where: (ref: Partial<CaseRef>) => string;
}) {
  const { t, i18n } = useTranslation();
  const money = useMoney();
  const queryClient = useQueryClient();
  const charge = useMutation({
    mutationFn: (caseId: string) =>
      apiPost<Ledger>(`/registrars/${l.id}/talons`, { case_id: caseId }),
    onSuccess: (data) => {
      queryClient.setQueryData(ledgerKey(l.id), data);
      void queryClient.invalidateQueries({ queryKey: ["registrars", "ledgers"] });
      void queryClient.invalidateQueries({ queryKey: ["finance"] });
    },
  });
  return (
    <section className="card">
      <h2 className="h2">{t("registrars.missingTitle", { number: l.missing_talons.length })}</h2>
      <p className="muted small">{t("registrars.missingHint")}</p>
      {charge.isError && <ErrorAlert error={charge.error} />}
      <div className="table-wrap">
        <table className="tbl">
          <tbody>
            {l.missing_talons.map((m) => (
              <tr key={m.case_id}>
                <td>
                  <Link to={`/app/clients/${m.contact_id}`}>{m.contact_name}</Link>
                  <div className="muted small">{where(m)}</div>
                </td>
                <td>
                  <CaseChip stage={m.stage} />
                </td>
                <td className="nowrap muted small">
                  {m.queued_on
                    ? t("registrars.queuedOn", { date: formatDate(m.queued_on, i18n.language) })
                    : t("registrars.notQueued")}
                </td>
                <td className="actions-cell">
                  {m.price !== null ? (
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={charge.isPending}
                      onClick={() => charge.mutate(m.case_id)}
                    >
                      {t("registrars.charge", {
                        amount: money(m.price),
                        currency: m.price_currency,
                      })}
                    </button>
                  ) : (
                    <span className="muted small">{t("registrars.noPrice")}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function PaymentActions({
  registrarId,
  paymentId,
  onEdit,
}: {
  registrarId: string;
  paymentId: string;
  onEdit: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => apiDelete<Ledger>(`/registrar-payments/${paymentId}`),
    onSuccess: (data) => {
      queryClient.setQueryData(ledgerKey(registrarId), data);
      void queryClient.invalidateQueries({ queryKey: ["registrars", "ledgers"] });
    },
  });
  return (
    <div className="actions">
      <button type="button" className="link-button" onClick={onEdit}>
        {t("reference.edit")}
      </button>
      <button
        type="button"
        className="link-button"
        disabled={remove.isPending}
        onClick={() => {
          if (window.confirm(t("registrars.confirmDelete"))) remove.mutate();
        }}
      >
        {t("vault.delete")}
      </button>
    </div>
  );
}

function PaymentForm({
  registrarId,
  currency,
  payment,
  onDone,
}: {
  registrarId: string;
  currency: string;
  payment: Line | null;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [paidOn, setPaidOn] = useState(payment?.on ?? todayIso());
  const [amount, setAmount] = useState(payment?.amount ?? "");
  const [method, setMethod] = useState<Method>(payment?.method ?? "transfer");
  const [forTalons, setForTalons] = useState(Boolean(payment?.talons));
  const [talons, setTalons] = useState(String(payment?.talons ?? 1));
  const [note, setNote] = useState(payment?.note ?? "");
  const save = useMutation({
    mutationFn: () => {
      const body = {
        paid_on: paidOn,
        amount: toDecimal(amount),
        method,
        talons: forTalons ? Number(talons) : null,
        note: note.trim() || null,
      };
      return payment
        ? apiPatch<Ledger>(`/registrar-payments/${payment.payment_id}`, body)
        : apiPost<Ledger>(`/registrars/${registrarId}/payments`, body);
    },
    onSuccess: (data) => {
      queryClient.setQueryData(ledgerKey(registrarId), data);
      void queryClient.invalidateQueries({ queryKey: ["registrars", "ledgers"] });
      onDone();
    },
  });

  return (
    <form
      className="card form-card"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <h2 className="h2">{t(payment ? "registrars.editPayment" : "registrars.addPayment")}</h2>
      <div className="form-grid">
        <label className="field">
          <span>{t("registrars.paidOn")}</span>
          <input type="date" required value={paidOn} onChange={(e) => setPaidOn(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("registrars.amount", { currency })}</span>
          <input
            inputMode="decimal"
            required
            pattern="[0-9 ]*([.,][0-9]{1,2})?"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>
        <label className="field">
          <span>{t("registrars.methodLabel")}</span>
          <select value={method} onChange={(e) => setMethod(e.target.value as Method)}>
            <option value="transfer">{t("registrars.method.transfer")}</option>
            <option value="cash">{t("registrars.method.cash")}</option>
          </select>
        </label>
        <label className="field">
          <span>{t("registrars.purpose")}</span>
          <select
            value={forTalons ? "talons" : "advance"}
            onChange={(e) => setForTalons(e.target.value === "talons")}
          >
            <option value="talons">{t("registrars.purposeTalons")}</option>
            <option value="advance">{t("registrars.purposeAdvance")}</option>
          </select>
        </label>
        {forTalons && (
          <label className="field">
            <span>{t("registrars.talonsCount")}</span>
            <input
              type="number"
              min={1}
              max={1000}
              required
              value={talons}
              onChange={(e) => setTalons(e.target.value)}
            />
          </label>
        )}
        <label className="field">
          <span>{t("registrars.note")}</span>
          <input maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>
      {save.isError && <ErrorAlert error={save.error} />}
      <div className="form-actions">
        <button type="submit" className="btn btn-p" disabled={save.isPending}>
          {t("common.save")}
        </button>
        <button type="button" className="btn" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState, type DragEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { apiGet, apiPost, errorCode } from "../../api";
import { useConfirm } from "../../components/ConfirmDialog";
import { useDisclosure } from "../../components/Disclosure";
import {
  CASE_STAGES,
  CREATE_CASE,
  enterResult,
  FROM_INDEX,
  formatDate,
  useCaseLists,
  type Board,
  type BoardCase,
  type BoardLead,
  type CaseStage,
} from "../../contacts";
import { byId, useCountries, useLocalName } from "../../reference";
import { CaseChip } from "./Cases";
import type { Filters } from "./ClientsPage";
import { todayIso } from "./ui";

// Kanban of the client index (backlog 1.5): clients after a consultation, then cases by
// stage. A submission group at one stage is one card and moves together. A card is easy
// to drop in the wrong column, so every drop asks first; a client from «Консультация»
// dropped on a stage opens the case form in the client's card (a case needs the place
// of submission and the applicants, which the board does not have). «Результат» takes a
// case only with its result (CRM audit 2026-11-18): a case without one dropped there
// opens its form at the result instead.

const DRAG_TYPE = "application/x-visacrm-cases";
const LEAD_DRAG_TYPE = "application/x-visacrm-lead";
const PERIOD_KEY = "visacrm.boardResultPeriod";

/** Which decisions «Результат» shows (app/core/cases.py → ResultPeriod): the last month by
 * default, half a year, a year, all of them, or the specialist's own dates. */
const RESULT_PERIODS = ["month", "half_year", "year", "all", "dates"] as const;
type ResultPeriod = (typeof RESULT_PERIODS)[number];
const PERIOD_MONTHS: Partial<Record<ResultPeriod, number>> = { month: 1, half_year: 6, year: 12 };
/** The period with its own dates as typed (ISO, or empty for an open end). */
type PeriodChoice = { period: ResultPeriod; from: string; to: string };
const DEFAULT_CHOICE: PeriodChoice = { period: "month", from: "", to: "" };
// The days the API takes (app/core/days.py): a year still being typed («0202») is not sent.
const FIRST_DAY = "1900-01-01";
const LAST_DAY = "2100-12-31";

function isDay(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    value >= FIRST_DAY &&
    value <= LAST_DAY
  );
}

/** The same day `months` calendar months earlier; the 31st becomes the last day of a
 * shorter month, as on the server. */
function monthsBefore(day: string, months: number): string {
  const [y, m, d] = day.split("-").map(Number);
  const lastDay = new Date(Date.UTC(y, m - months, 0)).getUTCDate();
  return new Date(Date.UTC(y, m - 1 - months, Math.min(d, lastDay))).toISOString().slice(0, 10);
}

// The chosen period is a per-browser convenience; storage may be unavailable.
function savedChoice(): PeriodChoice {
  try {
    const saved = JSON.parse(localStorage.getItem(PERIOD_KEY) ?? "null") as Record<
      string,
      unknown
    > | null;
    const period = RESULT_PERIODS.find((p) => p === saved?.period);
    if (!saved || !period) return DEFAULT_CHOICE;
    return {
      period,
      from: isDay(saved.from) ? saved.from : "",
      to: isDay(saved.to) ? saved.to : "",
    };
  } catch {
    return DEFAULT_CHOICE;
  }
}

function saveChoice(choice: PeriodChoice) {
  try {
    localStorage.setItem(PERIOD_KEY, JSON.stringify(choice));
  } catch {
    // The choice just is not remembered.
  }
}

/** The board's query for «Результат»: nothing for the default month; own dates in order,
 * an end not (yet) a whole date left open. */
function periodParams(choice: PeriodChoice, params: URLSearchParams) {
  if (choice.period === DEFAULT_CHOICE.period) return;
  params.set("result_period", choice.period);
  if (choice.period !== "dates") return;
  const reversed = isDay(choice.from) && isDay(choice.to) && choice.from > choice.to;
  const [from, to] = reversed ? [choice.to, choice.from] : [choice.from, choice.to];
  if (isDay(from)) params.set("result_from", from);
  if (isDay(to)) params.set("result_to", to);
}

/** What a case card carries while it is dragged; `noResult`: its first case without a
 * result, which «Результат» does not take. */
type CaseDrag = {
  ids: string[];
  from: CaseStage;
  names: string[];
  noResult: { caseId: string; contactId: string; name: string } | null;
};
/** What a card from «Консультация» carries. */
type LeadDrag = { contactId: string; name: string };

/** The board is searched by name on the page: its data has names, not phones. */
function matches(name: string, query: string): boolean {
  return name.toLocaleLowerCase().includes(query.toLocaleLowerCase());
}

/** Cases of one group (or a single case) at the same stage. */
type Card = { key: string; cases: BoardCase[] };

function cardsByStage(cases: BoardCase[]): Map<CaseStage, Card[]> {
  const cards = new Map<string, Card>();
  for (const c of [...cases].sort((a, b) => a.number - b.number)) {
    const key = `${c.group_id ?? c.id}:${c.stage}`;
    const card = cards.get(key) ?? { key, cases: [] };
    card.cases.push(c);
    cards.set(key, card);
  }
  const columns = new Map<CaseStage, Card[]>(CASE_STAGES.map((s) => [s, []]));
  for (const card of cards.values()) columns.get(card.cases[0].stage)?.push(card);
  return columns;
}

export function BoardView({ filters, query }: { filters: Filters; query: string }) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const hint = useDisclosure();
  const term = query.trim();
  const [choice, setChoice] = useState<PeriodChoice>(savedChoice);
  const choose = (next: PeriodChoice) => {
    setChoice(next);
    saveChoice(next);
  };
  const params = new URLSearchParams();
  if (filters.country) params.set("country_id", filters.country);
  if (filters.channel) params.set("channel_id", filters.channel);
  if (filters.registrar) params.set("registrar_id", filters.registrar);
  periodParams(choice, params);
  const key = ["cases", "board", params.toString()];
  const board = useQuery({
    queryKey: key,
    queryFn: () => apiGet<Board>(`/cases/board${params.toString() ? `?${params}` : ""}`),
    // Another period keeps the board on screen until the new one comes.
    placeholderData: keepPreviousData,
  });
  const [over, setOver] = useState<CaseStage | null>(null);

  const move = useMutation({
    mutationFn: ({ ids, stage }: { ids: string[]; stage: CaseStage }) =>
      apiPost<void>("/cases/stage", { case_ids: ids, stage }),
    // The card moves at once; the board is reloaded either way.
    onMutate: ({ ids, stage }) => {
      queryClient.setQueryData<Board>(key, (data) =>
        data && {
          ...data,
          cases: data.cases.map((c) => (ids.includes(c.id) ? { ...c, stage } : c)),
        },
      );
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["cases", "board"] });
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
    },
  });

  // A family's card stays whole when the name of any of its members matches; a case
  // opened through someone else's consultation is found by that name too.
  const columns = useMemo(() => {
    const cases = board.data?.cases ?? [];
    if (!term) return cardsByStage(cases);
    const hit = (c: BoardCase) =>
      matches(c.full_name, term) || (c.via !== null && matches(c.via.full_name, term));
    const found = cases.filter(hit);
    const groups = new Set(found.map((c) => c.group_id).filter(Boolean));
    return cardsByStage(cases.filter((c) => hit(c) || (c.group_id && groups.has(c.group_id))));
  }, [board.data, term]);
  const leads = (board.data?.leads ?? []).filter((lead) => !term || matches(lead.full_name, term));

  // What was dragged is read before the question: the drop's data is gone after it.
  const drop = (stage: CaseStage) => async (event: DragEvent) => {
    event.preventDefault();
    setOver(null);
    const lead = event.dataTransfer.getData(LEAD_DRAG_TYPE);
    if (lead) {
      const { contactId, name } = JSON.parse(lead) as LeadDrag;
      const yes = await confirm({
        message: t("index.confirmOpenCase", { name }),
        action: t("confirm.openCase"),
        tone: "neutral",
      });
      if (yes) {
        navigate(`/app/clients/${contactId}`, { state: { ...FROM_INDEX, ...CREATE_CASE } });
      }
      return;
    }
    const raw = event.dataTransfer.getData(DRAG_TYPE);
    if (!raw) return;
    const { ids, from, names, noResult } = JSON.parse(raw) as CaseDrag;
    if (from === stage) return;
    if (stage === "result" && noResult) {
      const yes = await confirm({
        message: t("index.confirmResult", { name: noResult.name }),
        action: t("confirm.enterResult"),
        tone: "neutral",
      });
      if (yes) {
        navigate(`/app/clients/${noResult.contactId}`, {
          state: { ...FROM_INDEX, ...enterResult(noResult.caseId) },
        });
      }
      return;
    }
    const question = t("index.confirmMove", {
      name: names.join(", "),
      from: t(`cases.stage.${from}`),
      to: t(`cases.stage.${stage}`),
    });
    if (await confirm({ message: question, action: t("confirm.move"), tone: "neutral" })) {
      move.mutate({ ids, stage });
    }
  };

  if (board.isPending) return <p className="muted">{t("common.loading")}</p>;
  if (board.isError) return <p className="alert alert-error">{t("errors.unknown")}</p>;

  return (
    <section aria-label={t("index.board")}>
      <button className="link-button hint-toggle" {...hint.button}>
        {t("index.boardHintTitle")}
      </button>
      <p className="muted small" {...hint.panel}>
        {t("index.boardHint")}
      </p>
      <PeriodBar choice={choice} onChange={choose} />
      {move.isError && (
        <div className="alert alert-error" role="alert">
          {t(`errors.${errorCode(move.error)}`, { defaultValue: t("errors.unknown") })}
        </div>
      )}
      <div className="board">
        <div className="board-col">
          <h2 className="board-title">
            {t("index.consultColumn")} <span className="muted">{leads.length}</span>
          </h2>
          {leads.map((lead) => (
            <LeadCard key={lead.contact_id} lead={lead} />
          ))}
        </div>
        {CASE_STAGES.map((stage) => {
          const cards = columns.get(stage) ?? [];
          return (
            <div
              key={stage}
              className={over === stage ? "board-col drop" : "board-col"}
              onDragOver={(e) => {
                const types = e.dataTransfer.types;
                if (!types.includes(DRAG_TYPE) && !types.includes(LEAD_DRAG_TYPE)) return;
                e.preventDefault();
                setOver(stage);
              }}
              onDragLeave={() => setOver((current) => (current === stage ? null : current))}
              onDrop={drop(stage)}
            >
              <h2 className="board-title">
                {t(`cases.stage.${stage}`)} <span className="muted">{cards.length}</span>
              </h2>
              {cards.map((card) => (
                <CaseCard key={card.key} card={card} />
              ))}
            </div>
          );
        })}
      </div>
    </section>
  );
}

/** Above the board, where it is seen without scrolling to «Результат» (the last column):
 * quick choices, all time, and «Период» that opens the fields of own dates. They start
 * from the period chosen before, so the board does not change until a date is. */
function PeriodBar({
  choice,
  onChange,
}: {
  choice: PeriodChoice;
  onChange: (choice: PeriodChoice) => void;
}) {
  const { t } = useTranslation();
  const label = useId();
  const pick = (period: ResultPeriod) => {
    if (period !== "dates") return onChange({ ...choice, period });
    if (choice.period === "dates") return;
    const today = todayIso();
    const months = PERIOD_MONTHS[choice.period];
    const from = choice.from || (months ? monthsBefore(today, months) : "");
    onChange({ period, from, to: choice.to || today });
  };
  const date = (field: "from" | "to") => (
    <label className="field board-date">
      <span>{t(field === "from" ? "index.resultFrom" : "index.resultTo")}</span>
      <input
        type="date"
        min={FIRST_DAY}
        max={LAST_DAY}
        value={choice[field]}
        onChange={(e) => onChange({ ...choice, [field]: e.target.value })}
      />
    </label>
  );

  return (
    <div className="board-period">
      <span id={label} className="board-period-label">
        {t("index.resultPeriod")}
      </span>
      <div className="view-switch" role="group" aria-labelledby={label}>
        {RESULT_PERIODS.map((period) => (
          <button
            key={period}
            type="button"
            aria-pressed={choice.period === period}
            className={choice.period === period ? "on" : undefined}
            onClick={() => pick(period)}
          >
            {t(`index.resultPeriods.${period}`)}
          </button>
        ))}
      </div>
      {choice.period === "dates" && (
        <div className="board-dates">
          {date("from")}
          {date("to")}
        </div>
      )}
    </div>
  );
}

function CaseCard({ card }: { card: Card }) {
  const { t, i18n } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const lists = useCaseLists();
  const [first] = card.cases;
  const purpose = lists.purposes.find((p) => p.id === first.purpose_id)?.name;
  const others = card.cases.length - 1;
  // Opened from someone else's consultation (pilot fix 2): «через Наумик Наталья».
  const via = card.cases.find((c) => c.via !== null)?.via;

  return (
    <article
      className="board-card"
      draggable
      onDragStart={(e) => {
        const open = card.cases.find((c) => c.outcome === null);
        const drag: CaseDrag = {
          ids: card.cases.map((c) => c.id),
          from: first.stage,
          names: card.cases.map((c) => c.full_name),
          noResult: open
            ? { caseId: open.id, contactId: open.contact_id, name: open.full_name }
            : null,
        };
        e.dataTransfer.setData(DRAG_TYPE, JSON.stringify(drag));
        e.dataTransfer.effectAllowed = "move";
      }}
    >
      <div>
        <Link to={`/app/clients/${first.contact_id}`} state={FROM_INDEX} draggable={false}>
          {first.full_name}
        </Link>
        {others > 0 && (
          <span className="muted" title={card.cases.slice(1).map((c) => c.full_name).join(", ")}>
            {" "}
            +{others}
          </span>
        )}
        {via && (
          <div className="muted small">
            {t("index.through")}{" "}
            <Link to={`/app/clients/${via.contact_id}`} state={FROM_INDEX} draggable={false}>
              {via.full_name}
            </Link>
          </div>
        )}
      </div>
      <div className="muted small">
        {[
          card.cases.map((c) => t("cases.number", { number: c.number })).join(", "),
          name.country(countryById.get(first.destination_id)),
          purpose,
        ]
          .filter(Boolean)
          .join(" · ")}
      </div>
      {first.outcome ? (
        <CaseChip stage={first.stage} outcome={first.outcome} />
      ) : (
        first.submission_on && (
          <div className="small">
            {t("index.nextSubmission", { date: formatDate(first.submission_on, i18n.language) })}
          </div>
        )
      )}
    </article>
  );
}

function LeadCard({ lead }: { lead: BoardLead }) {
  const { t, i18n } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  return (
    <article
      className="board-card lead"
      draggable
      onDragStart={(e) => {
        const drag: LeadDrag = { contactId: lead.contact_id, name: lead.full_name };
        e.dataTransfer.setData(LEAD_DRAG_TYPE, JSON.stringify(drag));
        e.dataTransfer.effectAllowed = "move";
      }}
    >
      <Link to={`/app/clients/${lead.contact_id}`} state={FROM_INDEX} draggable={false}>
        {lead.full_name}
      </Link>
      <div className="muted small">
        {[
          lead.held_on && formatDate(lead.held_on, i18n.language),
          lead.destination_ids.map((id) => name.country(countryById.get(id))).join(", "),
        ]
          .filter(Boolean)
          .join(" · ")}
      </div>
      <span className={lead.tab === "thinking" ? "chip st-orange" : "chip st-gray"}>
        {lead.tab === "thinking"
          ? t("contacts.decision.thinking")
          : lead.decision === "yes"
            ? t("index.decidedYes")
            : lead.held_on
              ? t("index.undecided")
              : t("index.newContact")}
      </span>
    </article>
  );
}

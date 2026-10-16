import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type DragEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { apiGet, apiPost, errorCode } from "../../api";
import {
  CASE_STAGES,
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

// Kanban of the client index (backlog 1.5): clients after a consultation, then cases by
// stage. A submission group at one stage is one card and moves together.

const DRAG_TYPE = "application/x-visacrm-cases";

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

export function BoardView({ filters }: { filters: Filters }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const params = new URLSearchParams();
  if (filters.country) params.set("country_id", filters.country);
  if (filters.channel) params.set("channel_id", filters.channel);
  if (filters.registrar) params.set("registrar_id", filters.registrar);
  const key = ["cases", "board", params.toString()];
  const board = useQuery({
    queryKey: key,
    queryFn: () => apiGet<Board>(`/cases/board${params.toString() ? `?${params}` : ""}`),
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

  const columns = useMemo(() => cardsByStage(board.data?.cases ?? []), [board.data]);

  const drop = (stage: CaseStage) => (event: DragEvent) => {
    event.preventDefault();
    setOver(null);
    const raw = event.dataTransfer.getData(DRAG_TYPE);
    if (!raw) return;
    const { ids, from } = JSON.parse(raw) as { ids: string[]; from: CaseStage };
    if (from !== stage) move.mutate({ ids, stage });
  };

  if (board.isPending) return <p className="muted">{t("common.loading")}</p>;
  if (board.isError) return <p className="alert alert-error">{t("errors.unknown")}</p>;

  return (
    <section aria-label={t("index.board")}>
      <p className="muted small">{t("index.boardHint")}</p>
      {move.isError && (
        <div className="alert alert-error" role="alert">
          {t(`errors.${errorCode(move.error)}`, { defaultValue: t("errors.unknown") })}
        </div>
      )}
      <div className="board">
        <div className="board-col">
          <h2 className="board-title">
            {t("index.consultColumn")} <span className="muted">{board.data.leads.length}</span>
          </h2>
          {board.data.leads.map((lead) => (
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
                if (!e.dataTransfer.types.includes(DRAG_TYPE)) return;
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

function CaseCard({ card }: { card: Card }) {
  const { t, i18n } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const lists = useCaseLists();
  const [first] = card.cases;
  const purpose = lists.purposes.find((p) => p.id === first.purpose_id)?.name;
  const others = card.cases.length - 1;

  return (
    <article
      className="board-card"
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(
          DRAG_TYPE,
          JSON.stringify({ ids: card.cases.map((c) => c.id), from: first.stage }),
        );
        e.dataTransfer.effectAllowed = "move";
      }}
    >
      <div>
        <Link to={`/app/clients/${first.contact_id}`} draggable={false}>
          {first.full_name}
        </Link>
        {others > 0 && (
          <span className="muted" title={card.cases.slice(1).map((c) => c.full_name).join(", ")}>
            {" "}
            +{others}
          </span>
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
    <article className="board-card lead">
      <Link to={`/app/clients/${lead.contact_id}`}>{lead.full_name}</Link>
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

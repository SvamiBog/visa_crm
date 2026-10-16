import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { apiGet, apiPost } from "../../api";
import {
  CASE_STAGES,
  TABS,
  formatDate,
  formatPhone,
  useCaseLists,
  useContactLists,
  type CaseStage,
  type ContactRow,
  type SearchPage,
  type Tab,
} from "../../contacts";
import { byId, useCountries, useLocalName, type Country } from "../../reference";
import { BoardView } from "./Board";
import { CaseChip } from "./Cases";
import { ContactForm } from "./ContactForm";

const PAGE_SIZE = 50;
const VIEW_KEY = "visacrm.clientsView";

type View = "list" | "board";
export type Filters = { country: string; stage: CaseStage | ""; channel: string; registrar: string };
const NO_FILTERS: Filters = { country: "", stage: "", channel: "", registrar: "" };

/** Waits until typing pauses, so every keystroke is not a request. */
function useDebounced(value: string, delay = 300): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

// The chosen view is a per-browser convenience; storage may be unavailable.
function savedView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === "board" ? "board" : "list";
  } catch {
    return "list";
  }
}

function saveView(view: View) {
  try {
    localStorage.setItem(VIEW_KEY, view);
  } catch {
    // The choice just is not remembered.
  }
}

export function useSortedCountries(): Country[] {
  const name = useLocalName();
  const countries = useCountries();
  return useMemo(
    () =>
      [...(countries.data ?? [])].sort((a, b) => name.country(a).localeCompare(name.country(b))),
    [countries.data, name],
  );
}

/** Client index: tabs, search, filters, a list of contacts or a board of cases by stage. */
export function ClientsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // Links from the home screen open a tab (?tab=work) or the new client form (?new=1).
  const [params] = useSearchParams();
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<Tab>(() => {
    const asked = params.get("tab");
    return TABS.find((key) => key === asked) ?? "all";
  });
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [view, setView] = useState<View>(savedView);
  const [formOpen, setFormOpen] = useState(params.get("new") === "1");
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<{ modules: string[] }>("/workspace"),
  });
  const withCases = workspace.data?.modules.includes("cases") ?? false;
  const shownView: View = withCases ? view : "list";

  const pick = (next: View) => {
    setView(next);
    saveView(next);
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("contacts.title")}</h1>
          <div className="muted">{t("contacts.subtitle")}</div>
        </div>
        {!formOpen && (
          <button type="button" className="btn btn-p" onClick={() => setFormOpen(true)}>
            {t("contacts.add")}
          </button>
        )}
      </header>

      {formOpen && (
        <ContactForm
          onCancel={() => setFormOpen(false)}
          onSaved={(contact) => navigate(`/app/clients/${contact.id}`)}
        />
      )}

      <div className="toolbar">
        {shownView === "list" && (
          <label className="field field-inline search-field">
            <span className="visually-hidden">{t("contacts.search")}</span>
            <input
              type="search"
              placeholder={t("contacts.searchPlaceholder")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        )}
        {withCases && (
          <div className="view-switch" role="group" aria-label={t("index.view")}>
            {(["list", "board"] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={shownView === v}
                className={shownView === v ? "on" : undefined}
                onClick={() => pick(v)}
              >
                {t(`index.${v}`)}
              </button>
            ))}
          </div>
        )}
      </div>

      <FilterBar
        filters={filters}
        onChange={setFilters}
        withCases={withCases}
        withStage={shownView === "list"}
      />

      {shownView === "list" ? (
        <ListView query={query} tab={tab} onTab={setTab} filters={filters} />
      ) : (
        <BoardView filters={filters} />
      )}
    </div>
  );
}

function FilterBar({
  filters,
  onChange,
  withCases,
  withStage,
}: {
  filters: Filters;
  onChange: (filters: Filters) => void;
  withCases: boolean;
  withStage: boolean;
}) {
  const { t } = useTranslation();
  const name = useLocalName();
  const countries = useSortedCountries();
  const contactLists = useContactLists();
  const caseLists = useCaseLists();
  const set = (changes: Partial<Filters>) => onChange({ ...filters, ...changes });
  const select = (label: string, value: string, setValue: (v: string) => void, options: ReactNode) => (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(e) => setValue(e.target.value)}>
        <option value="">{t("index.any")}</option>
        {options}
      </select>
    </label>
  );

  return (
    <div className="filter-bar">
      {select(
        t("index.country"),
        filters.country,
        (v) => set({ country: v }),
        countries.map((c) => (
          <option key={c.id} value={c.id}>
            {name.country(c)}
          </option>
        )),
      )}
      {withCases &&
        withStage &&
        select(
          t("index.stage"),
          filters.stage,
          (v) => set({ stage: v as CaseStage | "" }),
          CASE_STAGES.map((s) => (
            <option key={s} value={s}>
              {t(`cases.stage.${s}`)}
            </option>
          )),
        )}
      {select(
        t("contacts.channel"),
        filters.channel,
        (v) => set({ channel: v }),
        contactLists.channels.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        )),
      )}
      {withCases &&
        select(
          t("cases.registrarField"),
          filters.registrar,
          (v) => set({ registrar: v }),
          caseLists.registrars.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          )),
        )}
      {(filters.country || filters.stage || filters.channel || filters.registrar) && (
        <button type="button" className="btn btn-sm" onClick={() => onChange(NO_FILTERS)}>
          {t("index.reset")}
        </button>
      )}
    </div>
  );
}

function ListView({
  query,
  tab,
  onTab,
  filters,
}: {
  query: string;
  tab: Tab;
  onTab: (tab: Tab) => void;
  filters: Filters;
}) {
  const { t, i18n } = useTranslation();
  const term = useDebounced(query.trim());
  const name = useLocalName();
  const countries = useCountries();
  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const contactLists = useContactLists();
  const caseLists = useCaseLists();
  const channelById = useMemo(() => byId(contactLists.channels), [contactLists.channels]);
  const purposeById = useMemo(() => byId(caseLists.purposes), [caseLists.purposes]);

  // POST: the search text is personal data and must not end up in a URL.
  const body = {
    query: term,
    tab,
    country_id: filters.country || null,
    stage: filters.stage || null,
    channel_id: filters.channel || null,
    registrar_id: filters.registrar || null,
  };
  const contacts = useInfiniteQuery({
    queryKey: ["contacts", "search", body],
    queryFn: ({ pageParam }) =>
      apiPost<SearchPage>("/contacts/search", { ...body, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => (last.has_more ? pages.length * PAGE_SIZE : undefined),
  });
  const items = contacts.data?.pages.flatMap((p) => p.items) ?? [];
  const counts = contacts.data?.pages[0]?.counts;
  const date = (value: string | null) => formatDate(value, i18n.language);

  const where = (row: ContactRow) => {
    if (row.case) {
      return [
        name.country(countryById.get(row.case.destination_id)),
        purposeById.get(row.case.purpose_id ?? "")?.name,
      ]
        .filter(Boolean)
        .join(" · ");
    }
    return (row.consultation?.destination_ids ?? [])
      .map((id) => name.country(countryById.get(id)))
      .join(", ");
  };

  const next = (row: ContactRow) => {
    if (row.case?.outcome) return null;
    if (row.case?.submission_on) return t("index.nextSubmission", { date: date(row.case.submission_on) });
    if (row.consultation && !row.case) {
      return t("index.nextConsultation", { date: date(row.consultation.held_on) });
    }
    return null;
  };

  return (
    <>
      <div className="tabs" role="tablist" aria-label={t("index.tabs")}>
        {TABS.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "tab active" : "tab"}
            onClick={() => onTab(key)}
          >
            {t(`index.tab.${key}`)}
            {counts && <span className="tab-count">{counts[key]}</span>}
          </button>
        ))}
      </div>
      <section className="card">
        {contacts.isPending && <p className="muted">{t("common.loading")}</p>}
        {contacts.isError && <p className="alert alert-error">{t("errors.unknown")}</p>}
        {contacts.data && items.length === 0 && (
          <p className="muted">{term ? t("contacts.notFound") : t("index.empty")}</p>
        )}
        {items.length > 0 && (
          <div className="table-wrap">
            <table className="tbl clients-table">
              <thead>
                <tr>
                  <th>{t("index.colClient")}</th>
                  <th>{t("index.colWhere")}</th>
                  <th>{t("index.colStage")}</th>
                  <th>{t("index.colNext")}</th>
                  <th>{t("contacts.channel")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <Link to={`/app/clients/${row.id}`}>{row.full_name}</Link>
                      <div className="muted small">
                        {row.telegram ? `@${row.telegram}` : formatPhone(row.phone)}
                      </div>
                    </td>
                    <td>{where(row)}</td>
                    <td>
                      <RowStage row={row} />
                    </td>
                    <td className="nowrap">{next(row)}</td>
                    <td className="muted">{channelById.get(row.lead_channel_id ?? "")?.name}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {contacts.hasNextPage && (
          <button
            type="button"
            className="btn load-more"
            disabled={contacts.isFetchingNextPage}
            onClick={() => void contacts.fetchNextPage()}
          >
            {t("audit.loadMore")}
          </button>
        )}
      </section>
    </>
  );
}

/** The case's stage or result; without a case, where the consultation ended. */
function RowStage({ row }: { row: ContactRow }) {
  const { t } = useTranslation();
  if (row.case) {
    return (
      <span className="stage-cell">
        <CaseChip stage={row.case.stage} outcome={row.case.outcome} />
        {row.case.group_size > 1 && (
          <span className="muted small">{t("index.groupSize", { count: row.case.group_size })}</span>
        )}
        {row.cases_in_work > 1 && (
          <span className="muted small">{t("index.casesInWork", { count: row.cases_in_work })}</span>
        )}
      </span>
    );
  }
  const decision = row.consultation?.decision;
  if (!row.consultation) return <span className="chip st-gray">{t("index.newContact")}</span>;
  if (decision === "thinking") return <span className="chip st-orange">{t("contacts.decision.thinking")}</span>;
  if (decision === "yes") return <span className="chip st-green">{t("index.decidedYes")}</span>;
  if (decision === "no") return <span className="chip st-gray">{t("contacts.decision.no")}</span>;
  return <span className="chip st-gray">{t("index.undecided")}</span>;
}

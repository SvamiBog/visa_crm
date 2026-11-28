import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ApiError,
  apiDelete,
  apiDownload,
  apiGet,
  apiPatch,
  apiPost,
  errorCode,
} from "../../api";
import { useMe } from "../../auth";
import { useConfirm } from "../../components/ConfirmDialog";
import {
  caseToEnterResult,
  contactKey,
  FROM_INDEX,
  formatDate,
  formatPhone,
  openedFromIndex,
  openedToCreateCase,
  telegramUrl,
  useContactLists,
  type Consultation,
  type ContactCard,
  type ContactDeletion,
  type ContactDocument,
  type DocumentKind,
  type Note,
} from "../../contacts";
import { formatSize, readBase64 } from "../../files";
import { byId } from "../../reference";
import { contentLines } from "../../deletion";
import { deferDelete, useDeleting } from "../../undo";
import { useWorkspace } from "../../workspace";
import { AttentionList } from "../cabinet/Attention";
import { ContactVault } from "../vault/Vault";
import { CARD_TABS, TAB_PARAM, caseToShow, cardPath, tabOf, type CardTab } from "./cardTabs";
import { CaseChip, Cases } from "./Cases";
import { useMoney } from "./Finance";
import { ContactForm } from "./ContactForm";
import { PassportCard } from "./Passport";
import { ContactTemplates } from "./ContactTemplates";
import { Consultations, Hints, QuestionnaireCard } from "./Consultations";
import { ContactTrips } from "./Trips";
import { useCaseName } from "./ui";

function ErrorAlert({ error }: { error: unknown }) {
  const { t } = useTranslation();
  return (
    <div className="alert alert-error" role="alert">
      {t(`errors.${errorCode(error)}`, { defaultValue: t("errors.unknown") })}
    </div>
  );
}

/** «← Все клиенты»: back to the list as it was when the card was opened from it. */
function BackToList() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const fromIndex = openedFromIndex(location.state);
  return (
    <Link
      to="/app/clients"
      className="small"
      onClick={(event) => {
        if (!fromIndex) return;
        event.preventDefault();
        navigate(-1);
      }}
    >
      ← {t("contacts.backToList")}
    </Link>
  );
}

/** The card's own things the question before its deletion counts, after its cases and
 * consultations. */
const CARD_COUNTED = ["notes", "vault_entries", "documents", "trips", "templates"] as const;

/** The module each tab needs; «Клиент» and «Паспорт и документы» are always there, and
 * «Кейсы» is there when the card has its cases (null without the module). */
const TAB_MODULES: Partial<Record<CardTab, string>> = {
  trips: "stay_calculator",
  vault: "vault",
};

/** How far from the row's edge an open tab is scrolled to, the page's side gutter. */
const TAB_SCROLL_MARGIN = 16;

/** One tab's content. Every tab stays mounted and the others are only hidden, so what is
 * typed in a form on one tab is still there after a look at another (backlog 1.15.17). */
function TabPanel({ tab, shown, children }: { tab: CardTab; shown: boolean; children: ReactNode }) {
  return (
    <div
      role="tabpanel"
      id={`card-panel-${tab}`}
      aria-labelledby={`card-tab-${tab}`}
      className="card-panel"
      tabIndex={0}
      hidden={!shown}
    >
      {children}
    </div>
  );
}

/** Every card is the one route /app/clients/:id, so going to another card inside the app
 * (a link, «back», a reminder) keeps the page and changes only the id. The key makes the
 * whole card anew for each id: no open form, draft or unfolded case of one client is
 * carried to another and saved there (backlog A10). */
export function ContactPage() {
  const { id = "" } = useParams();
  return <ContactView key={id} id={id} />;
}

/** The client's card: contact details, questionnaire and its checks, cases,
 * consultations, paperwork, the notes timeline. Clients are not linked to each other
 * (backlog 1.15.11): a family filed together is a submission group of their cases. */
function ContactView({ id }: { id: string }) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const money = useMoney();
  const base = useWorkspace().data?.base_currency ?? "";
  const queryClient = useQueryClient();
  const card = useQuery({
    queryKey: contactKey(id),
    queryFn: () => apiGet<ContactCard>(`/contacts/${id}`),
  });
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<{ modules: string[]; file_storage: boolean }>("/workspace"),
  });
  const [editing, setEditing] = useState(false);
  // «Создать кейс» from a consultation, or a new case without one.
  const [creatingCase, setCreatingCase] = useState<Consultation | "blank" | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["contacts"] });
  const navigate = useNavigate();
  const location = useLocation();
  const [search] = useSearchParams();
  const modules = workspace.data?.modules;
  const hasCases = card.data?.cases != null;
  const tabs = useMemo(
    () =>
      CARD_TABS.filter((name) => {
        if (name === "cases") return hasCases;
        const needs = TAB_MODULES[name];
        return needs === undefined || modules?.includes(needs) === true;
      }),
    [modules, hasCases],
  );
  // No tab, an unknown one or one of a module turned off: «Клиент». A tab of a module
  // (trips, vault) waits for the modules, so a tab is never shown without its panel.
  const named = tabOf(search);
  const tab: CardTab = named !== null && tabs.includes(named) ? named : "client";
  // A switch replaces the history entry instead of adding one and keeps its state, so
  // «← Все клиенты» still goes back to the list as it was, after any number of switches.
  const open = (next: CardTab, state: unknown = location.state) =>
    navigate(
      { pathname: location.pathname, search: next === "client" ? "" : `?${TAB_PARAM}=${next}` },
      { replace: true, state },
    );
  // The request in the history state is done once: it leaves the history with the switch,
  // so that a reload or «back» does not do it again.
  const keptState = () => (openedFromIndex(location.state) ? FROM_INDEX : null);
  // A case asked for from another tab or page: «Кейсы» unfold it, bring it into view and
  // put the focus on its heading, each ask anew.
  const [askedCase, setAskedCase] = useState<{ id: string; times: number } | null>(null);
  const showCaseOnTab = (caseId: string) => {
    open("cases");
    setAskedCase((last) => ({ id: caseId, times: (last?.times ?? 0) + 1 }));
  };
  // «Создать кейс», «+ Кейс» of a consultation: the form opens on «Кейсы».
  const createCase = (value: Consultation | "blank" | null) => {
    if (value !== null) open("cases");
    setCreatingCase(value);
  };
  // A client dropped from «Консультация» on a stage of the board: the case form opens
  // once on «Кейсы», from the latest consultation.
  useEffect(() => {
    if (!card.data || !openedToCreateCase(location.state)) return;
    // Not for a client who already has a case in work, their own or opened from their
    // consultation (maybe for the family): the cases show instead, a new one is «+ Кейс»
    // (pilot fix 8).
    if (card.data.cases !== null) {
      const [latest] = card.data.consultations;
      const inWork = [...card.data.cases, ...(latest?.cases ?? [])].some(
        (c) => c.stage !== "result",
      );
      if (!inWork) setCreatingCase(latest && latest.decision !== "no" ? latest : "blank");
    }
    open(card.data.cases !== null ? "cases" : tab, keptState());
  }, [card.data, location.state, location.pathname, navigate]);
  // A case without a result dropped on «Результат» of the board: its form opens at the
  // result, once, the same way.
  const [resultCaseId, setResultCaseId] = useState<string | null>(null);
  useEffect(() => {
    const caseId = caseToEnterResult(location.state);
    if (!card.data || caseId === null) return;
    setResultCaseId(caseId);
    open(hasCases ? "cases" : tab, keptState());
  }, [card.data, location.state, location.pathname, navigate]);
  // A case asked for by another page (a case card of the board, «кейс открыт на …» in the
  // index): «Кейсы» on that case, once.
  useEffect(() => {
    const caseId = caseToShow(location.state);
    if (!card.data || caseId === null) return;
    setAskedCase((last) => ({ id: caseId, times: (last?.times ?? 0) + 1 }));
    open(hasCases ? "cases" : tab, keptState());
  }, [card.data, location.state, location.pathname, navigate]);
  // The card goes with all the client's history (decisions.md → «Удаление кейса и
  // карточки со всем содержимым»).
  const remove = useMutation({
    mutationFn: () => apiDelete(`/contacts/${id}`),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: contactKey(id) });
      void refresh();
      navigate("/app/clients");
    },
  });
  // The question lists everything that goes with this card, read when «Удалить
  // карточку» is pressed (pilot remark 1.15.13): its cases with what they hold, then the
  // card's own consultations, accounts, files and the rest. The button stays enabled
  // while the question is open, so the focus can go back to it.
  const ask = useMutation({
    mutationFn: async (name: string) => {
      const what = await apiGet<ContactDeletion>(`/contacts/${id}/deletion`);
      const numbers = what.cases.map((number) => t("cases.number", { number })).join(", ");
      const items = [
        ...(what.cases.length > 0
          ? [
              t(what.cases.length === 1 ? "contacts.deletedWith.case" : "contacts.deletedWith.cases", {
                numbers,
              }),
            ]
          : []),
        ...contentLines(what, t, (amount) => `${money(amount)} ${base}`),
        ...(what.consultations > 0
          ? [
              what.paid_consultations > 0
                ? t("contacts.deletedWith.consultationsPaid", {
                    n: what.consultations,
                    paid: what.paid_consultations,
                  })
                : t("contacts.deletedWith.consultations", { n: what.consultations }),
            ]
          : []),
        ...CARD_COUNTED.filter((key) => what[key] > 0).map((key) =>
          t(`contacts.deletedWith.${key}`, { n: what[key] }),
        ),
        ...(["passport", "questionnaire"] as const)
          .filter((key) => what[key])
          .map((key) => t(`contacts.deletedWith.${key}`)),
      ];
      return confirm({
        message: t(items.length > 0 ? "contacts.confirmDeleteCardWith" : "contacts.confirmDeleteCard", {
          name,
        }),
        items,
        note: t("contacts.deleteCardNote"),
        action: t("contacts.deleteCard"),
      });
    },
    onMutate: () => remove.reset(),
    onSuccess: (yes) => {
      if (yes) remove.mutate();
    },
    onError: () => void queryClient.invalidateQueries({ queryKey: contactKey(id) }),
  });

  if (card.isPending) return <p className="muted">{t("common.loading")}</p>;
  if (card.isError) {
    return (
      <div className="page">
        <ErrorAlert error={card.error} />
        <BackToList />
      </div>
    );
  }
  const { contact, notes } = card.data;

  return (
    <div className="page">
      <BackToList />
      <header className="page-header">
        <div className="contact-head">
          <span className="avatar" aria-hidden="true">
            {initials(contact.full_name)}
          </span>
          <div>
            <h1>{contact.full_name}</h1>
            {contact.full_name_latin && (
              <div className="muted">
                {contact.full_name_latin} · {t("contacts.asInPassport")}
              </div>
            )}
          </div>
        </div>
        <div className="actions">
          {telegramUrl(contact.telegram) && (
            <a className="btn btn-telegram" href={telegramUrl(contact.telegram)!} target="_blank" rel="noreferrer">
              {t("contacts.writeTelegram")}
            </a>
          )}
          {!editing && (
            <button type="button" className="btn" onClick={() => setEditing(true)}>
              {t("reference.edit")}
            </button>
          )}
          {card.data.can_delete && !editing && (
            <button
              type="button"
              className="btn btn-danger"
              disabled={remove.isPending}
              onClick={() => ask.mutate(contact.full_name)}
            >
              {t("contacts.deleteCard")}
            </button>
          )}
        </div>
      </header>
      {ask.isError && <ErrorAlert error={ask.error} />}
      {remove.isError && <ErrorAlert error={remove.error} />}

      {editing && (
        <ContactForm
          contact={contact}
          passport={card.data.questionnaire}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            void refresh();
          }}
        />
      )}

      <Hints hints={card.data.hints} />
      {workspace.data?.modules.includes("tasks") && <AttentionList contactId={contact.id} />}

      <CardTabs tabs={tabs} current={tab} casesCount={card.data.cases?.length ?? 0} onOpen={open} />

      <TabPanel tab="client" shown={tab === "client"}>
        <div className="card-grid">
          <div className="column">
            <ContactDetails card={card.data} onShowCase={showCaseOnTab} />
            <QuestionnaireCard card={card.data} onOpenCase={showCaseOnTab} />
          </div>
          <div className="column">
            <Consultations
              card={card.data}
              onCreateCase={card.data.cases !== null ? createCase : undefined}
            />
            <Timeline contactId={contact.id} notes={notes} />
          </div>
        </div>
      </TabPanel>
      {card.data.cases !== null && (
        <TabPanel tab="cases" shown={tab === "cases"}>
          <Cases
            card={card.data}
            creating={creatingCase}
            onCreating={createCase}
            resultCaseId={resultCaseId}
            asked={askedCase}
          />
        </TabPanel>
      )}
      <TabPanel tab="documents" shown={tab === "documents"}>
        <div className="card-grid">
          <div className="column">
            <PassportCard card={card.data} />
          </div>
          <div className="column">
            <Paperwork card={card.data} fileStorage={workspace.data?.file_storage === true} />
            {modules?.includes("document_templates") && (
              <ContactTemplates contactId={contact.id} cases={card.data.cases ?? []} />
            )}
          </div>
        </div>
      </TabPanel>
      {tabs.includes("trips") && (
        <TabPanel tab="trips" shown={tab === "trips"}>
          <ContactTrips contactId={contact.id} />
        </TabPanel>
      )}
      {tabs.includes("vault") && (
        <TabPanel tab="vault" shown={tab === "vault"}>
          <ContactVault contactId={contact.id} cases={card.data.cases ?? []} />
        </TabPanel>
      )}
    </div>
  );
}

/** The card's tabs, like those of «Справочники». On a narrow screen they keep one row
 * that scrolls sideways, and the open tab is scrolled into it. Arrow keys move between
 * the tabs the usual way of a tab list. */
function CardTabs({
  tabs,
  current,
  casesCount,
  onOpen,
}: {
  tabs: readonly CardTab[];
  current: CardTab;
  casesCount: number;
  onOpen: (tab: CardTab) => void;
}) {
  const { t } = useTranslation();
  const listRef = useRef<HTMLDivElement>(null);
  // Sideways only: the page itself does not move when a tab opens.
  useEffect(() => {
    const list = listRef.current;
    const button = list?.querySelector<HTMLElement>(`#card-tab-${current}`);
    if (!list || !button) return;
    const row = list.getBoundingClientRect();
    const box = button.getBoundingClientRect();
    if (box.left < row.left) list.scrollLeft -= row.left - box.left + TAB_SCROLL_MARGIN;
    else if (box.right > row.right) list.scrollLeft += box.right - row.right + TAB_SCROLL_MARGIN;
  }, [current, tabs]);
  const step = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = tabs.indexOf(current);
    const next =
      event.key === "ArrowRight"
        ? tabs[(index + 1) % tabs.length]
        : event.key === "ArrowLeft"
          ? tabs[(index - 1 + tabs.length) % tabs.length]
          : event.key === "Home"
            ? tabs[0]
            : event.key === "End"
              ? tabs[tabs.length - 1]
              : undefined;
    if (next === undefined) return;
    event.preventDefault();
    onOpen(next);
    listRef.current?.querySelector<HTMLElement>(`#card-tab-${next}`)?.focus();
  };
  return (
    <div
      ref={listRef}
      className="tabs tabs-scroll"
      role="tablist"
      aria-label={t("cardTabs.label")}
      onKeyDown={step}
    >
      {tabs.map((name) => (
        <button
          key={name}
          id={`card-tab-${name}`}
          type="button"
          role="tab"
          aria-selected={current === name}
          aria-controls={`card-panel-${name}`}
          tabIndex={current === name ? 0 : -1}
          // «Кейсы 2» to a screen reader, not «Кейсы2».
          aria-label={name === "cases" ? `${t("cardTabs.cases")} ${casesCount}` : undefined}
          className={current === name ? "tab active" : "tab"}
          onClick={() => onOpen(name)}
        >
          {t(`cardTabs.${name}`)}
          {name === "cases" && <span className="tab-count">{casesCount}</span>}
        </button>
      ))}
    </div>
  );
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="kv">
      <span className="muted">{label}</span>
      <span>{children || "—"}</span>
    </div>
  );
}

function ContactDetails({
  card,
  onShowCase,
}: {
  card: ContactCard;
  onShowCase: (caseId: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const caseName = useCaseName();
  const lists = useContactLists();
  const channels = useMemo(() => byId(lists.channels), [lists.channels]);
  const partners = useMemo(() => byId(lists.partners), [lists.partners]);
  const c = card.contact;
  const telegram = telegramUrl(c.telegram);
  return (
    <section className="card">
      <h2 className="h2">{t("contacts.details")}</h2>
      <Row label={t("contacts.phone")}>
        {c.phone && <a href={`tel:${c.phone}`}>{formatPhone(c.phone)}</a>}
      </Row>
      <Row label={t("contacts.telegram")}>
        {c.telegram &&
          (telegram ? (
            <a href={telegram} target="_blank" rel="noreferrer">
              @{c.telegram}
            </a>
          ) : (
            c.telegram
          ))}
      </Row>
      {c.messenger && (
        <Row label={t(`contacts.messengers.${c.messenger}`)}>{c.messenger_contact}</Row>
      )}
      <Row label={t("auth.email")}>{c.email}</Row>
      <Row label={t("contacts.birthDate")}>{formatDate(c.birth_date, i18n.language)}</Row>
      <Row label={t("contacts.channel")}>{channels.get(c.lead_channel_id ?? "")?.name}</Row>
      <Row label={t("contacts.partner")}>{partners.get(c.partner_id ?? "")?.name}</Row>
      <Row label={t("contacts.firstContact")}>
        {new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium" }).format(
          new Date(c.created_at),
        )}
      </Row>
      {/* The client's cases in short, so that a case is seen on the first tab; each opens
          on «Кейсы» (backlog 1.15.17). */}
      {card.cases !== null && card.cases.length > 0 && (
        <nav className="contact-cases case-list" aria-label={t("cardTabs.casesList")}>
          <h3 className="h3">{t("cases.title")}</h3>
          <ol>
            {card.cases.map((visaCase) => (
              <li key={visaCase.id}>
                <a
                  href={cardPath(c.id, "cases")}
                  onClick={(e) => {
                    // Ctrl-, Shift- or middle click opens «Кейсы» in a new tab the browser's way.
                    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                    e.preventDefault();
                    onShowCase(visaCase.id);
                  }}
                >
                  {caseName(visaCase)}
                </a>
                <CaseChip stage={visaCase.stage} outcome={visaCase.outcome} />
              </li>
            ))}
          </ol>
        </nav>
      )}
    </section>
  );
}

function Paperwork({ card, fileStorage }: { card: ContactCard; fileStorage: boolean }) {
  const { t, i18n } = useTranslation();
  const c = card.contact;
  // A paper with a file but no date yet is not flagged as missing.
  const hasFiles = (kind: DocumentKind) => card.documents.some((d) => d.kind === kind);
  const files = (kind: DocumentKind) => (
    <DocumentFiles
      contactId={c.id}
      kind={kind}
      documents={card.documents.filter((d) => d.kind === kind)}
      fileStorage={fileStorage}
    />
  );
  return (
    <section className="card">
      <h2 className="h2">{t("contacts.paperwork")}</h2>
      <Row label={t("contacts.consent")}>
        {c.consent_signed_on ? (
          t("contacts.signedOn", { date: formatDate(c.consent_signed_on, i18n.language) })
        ) : (
          !hasFiles("consent") && (
            <span className="chip st-orange">{t("contacts.consentMissing")}</span>
          )
        )}
      </Row>
      {files("consent")}
      <Row label={t("contacts.contract")}>
        {c.contract_signed_on || c.contract_number ? (
          [
            c.contract_number && `№ ${c.contract_number}`,
            c.contract_signed_on && formatDate(c.contract_signed_on, i18n.language),
          ]
            .filter(Boolean)
            .join(" · ")
        ) : (
          !hasFiles("contract") && (
            <span className="chip st-orange">{t("contacts.contractMissing")}</span>
          )
        )}
      </Row>
      {files("contract")}
      <p className="muted small">
        {fileStorage ? t("contacts.documentsHint") : t("contacts.documentsOff")}
      </p>
    </section>
  );
}

const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024;
const DOCUMENT_TYPES = [
  ".pdf,.jpg,.jpeg,.png,.webp,.heic,.heif",
  "application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif",
].join(",");

/** Files of one paper: download, attach, delete. The server checks the type by content
 * and keeps the file encrypted; every download goes to the audit log. */
function DocumentFiles({
  contactId,
  kind,
  documents,
  fileStorage,
}: {
  contactId: string;
  kind: DocumentKind;
  documents: ContactDocument[];
  fileStorage: boolean;
}) {
  const { t, i18n } = useTranslation();
  const confirm = useConfirm();
  const me = useMe();
  const queryClient = useQueryClient();
  const [error, setError] = useState<unknown>(null);
  // A new input after each pick: choosing the same file again fires onChange.
  const [inputKey, setInputKey] = useState(0);
  const refresh = () => queryClient.invalidateQueries({ queryKey: contactKey(contactId) });
  const url = `/contacts/${contactId}/documents`;

  const add = useMutation({
    mutationFn: async (file: File) =>
      apiPost<ContactDocument>(url, {
        kind,
        file_name: file.name.slice(-255),
        content: await readBase64(file),
      }),
    onSuccess: () => void refresh(),
    onError: setError,
  });
  const download = useMutation({
    mutationFn: (document: ContactDocument) =>
      apiDownload(`${url}/${document.id}`, document.file_name),
    onError: setError,
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiDelete<void>(`${url}/${id}`),
    onSuccess: () => void refresh(),
    onError: setError,
  });

  const choose = (file: File | undefined) => {
    setInputKey((key) => key + 1);
    setError(null);
    if (!file) return;
    if (file.size > MAX_DOCUMENT_BYTES) {
      setError(new ApiError(413, "document_too_large"));
      return;
    }
    add.mutate(file);
  };
  const mayDelete = (document: ContactDocument) =>
    me.data?.role === "specialist" || document.uploaded_by === me.data?.id;
  const added = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium" });

  if (documents.length === 0 && !fileStorage) return null;
  return (
    <div className="documents">
      {documents.length > 0 && (
        <ul className="plain-list">
          {documents.map((document) => (
            <li key={document.id}>
              <span>
                <button
                  type="button"
                  className="link-button"
                  title={t("contacts.downloadDocument")}
                  disabled={download.isPending}
                  onClick={() => {
                    setError(null);
                    download.mutate(document);
                  }}
                >
                  {document.file_name}
                </button>
                <span className="muted small">
                  {" · "}
                  {formatSize(document.size_bytes, i18n.language)} ·{" "}
                  {added.format(new Date(document.created_at))}
                </span>
              </span>
              {mayDelete(document) && (
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={remove.isPending}
                  onClick={async () => {
                    setError(null);
                    const yes = await confirm({
                      message: t("contacts.confirmDeleteDocument", { name: document.file_name }),
                      action: t("confirm.delete"),
                    });
                    if (yes) remove.mutate(document.id);
                  }}
                >
                  {t("contacts.deleteDocument")}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {fileStorage && (
        <label className="btn btn-sm file-button">
          {add.isPending ? t("contacts.uploadingDocument") : t(`contacts.attach.${kind}`)}
          <input
            key={inputKey}
            type="file"
            className="visually-hidden"
            accept={DOCUMENT_TYPES}
            disabled={add.isPending}
            onChange={(e) => choose(e.target.files?.[0])}
          />
        </label>
      )}
      {error !== null && <ErrorAlert error={error} />}
    </div>
  );
}

function Timeline({ contactId, notes }: { contactId: string; notes: Note[] }) {
  const { t, i18n } = useTranslation();
  const deleting = useDeleting();
  const me = useMe();
  const queryClient = useQueryClient();
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<{ timezone: string }>("/workspace"),
  });
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<{ id: string; body: string } | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: contactKey(contactId) });
  const url = `/contacts/${contactId}/notes`;

  // The field is emptied on send, not on success: text typed while saving is kept.
  // On an error the sent text comes back into the field.
  const add = useMutation({
    mutationFn: (body: string) => apiPost<Note>(url, { body }),
    onSuccess: () => void refresh(),
    onError: (_error, body) => setDraft((current) => (current.trim() ? current : body)),
  });
  const edit = useMutation({
    mutationFn: (note: { id: string; body: string }) =>
      apiPatch<Note>(`${url}/${note.id}`, { body: note.body }),
    onSuccess: () => {
      setEditing(null);
      void refresh();
    },
  });
  // Resolves once the list is fetched again, so the struck-through note does not flash
  // back before it goes.
  const remove = useMutation({
    mutationFn: (id: string) => apiDelete<void>(`${url}/${id}`),
    onSuccess: () => refresh(),
  });

  const when = new Intl.DateTimeFormat(i18n.language, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: workspace.data?.timezone,
  });
  const mayChange = (note: Note) =>
    me.data?.role === "specialist" || note.author_id === me.data?.id;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body = draft;
    setDraft("");
    add.mutate(body);
  };

  return (
    <section className="card timeline">
      <h2 className="h2">{t("contacts.timeline")}</h2>
      <form className="form-card" onSubmit={submit}>
        <label className="field">
          <span className="visually-hidden">{t("contacts.newNote")}</span>
          <textarea
            rows={3}
            maxLength={5000}
            placeholder={t("contacts.notePlaceholder")}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        </label>
        {add.isError && <ErrorAlert error={add.error} />}
        <div className="form-actions">
          <button type="submit" className="btn btn-sm" disabled={add.isPending || !draft.trim()}>
            {t("contacts.addNote")}
          </button>
        </div>
      </form>
      {notes.length === 0 && <p className="muted small">{t("contacts.noNotes")}</p>}
      <ol className="notes">
        {notes.map((note) => (
          <li key={note.id} className={deleting.has(`${url}/${note.id}`) ? "deleting" : undefined}>
            <div className="muted small">
              {when.format(new Date(note.created_at))}
              {note.author_email && ` · ${note.author_email}`}
              {note.edited_at && ` · ${t("contacts.edited")}`}
            </div>
            {editing?.id === note.id ? (
              <form
                className="form-card"
                onSubmit={(e) => {
                  e.preventDefault();
                  edit.mutate(editing);
                }}
              >
                <textarea
                  aria-label={t("contacts.newNote")}
                  rows={3}
                  maxLength={5000}
                  value={editing.body}
                  onChange={(e) => setEditing({ id: note.id, body: e.target.value })}
                />
                <div className="form-actions">
                  <button type="submit" className="btn btn-sm btn-p" disabled={edit.isPending || !editing.body.trim()}>
                    {t("common.save")}
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => setEditing(null)}>
                    {t("common.cancel")}
                  </button>
                </div>
              </form>
            ) : (
              <p className="note-body">{note.body}</p>
            )}
            {mayChange(note) && editing?.id !== note.id && (
              <div className="note-actions">
                <button
                  type="button"
                  className="link-button"
                  disabled={deleting.has(`${url}/${note.id}`)}
                  onClick={() => setEditing({ id: note.id, body: note.body })}
                >
                  {t("contacts.editNote")}
                </button>
                <button
                  type="button"
                  className="link-button"
                  disabled={deleting.has(`${url}/${note.id}`)}
                  onClick={() =>
                    deferDelete({
                      key: `${url}/${note.id}`,
                      message: t("undo.noteDeleted"),
                      commit: () => remove.mutateAsync(note.id),
                    })
                  }
                >
                  {t("contacts.deleteNote")}
                </button>
              </div>
            )}
          </li>
        ))}
      </ol>
      {edit.isError && <ErrorAlert error={edit.error} />}
    </section>
  );
}

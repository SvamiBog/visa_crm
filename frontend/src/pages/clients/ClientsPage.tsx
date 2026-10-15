import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { apiPost } from "../../api";
import { byId } from "../../reference";
import {
  formatPhone,
  useContactLists,
  type ContactListItem,
} from "../../contacts";
import { ContactForm } from "./ContactForm";

type Page = { items: ContactListItem[]; has_more: boolean };
const PAGE_SIZE = 50;

/** Waits until typing pauses, so every keystroke is not a request. */
function useDebounced(value: string, delay = 300): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

/** Client list with search by name, phone and Telegram. Tabs and kanban come in 1.5. */
export function ClientsPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const term = useDebounced(query.trim());
  const lists = useContactLists();
  const channelById = useMemo(() => byId(lists.channels), [lists.channels]);

  // POST: the search text is personal data and must not end up in a URL.
  const contacts = useInfiniteQuery({
    queryKey: ["contacts", "search", term],
    queryFn: ({ pageParam }) =>
      apiPost<Page>("/contacts/search", { query: term, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => (last.has_more ? pages.length * PAGE_SIZE : undefined),
  });
  const items = contacts.data?.pages.flatMap((p) => p.items) ?? [];
  const date = new Intl.DateTimeFormat(i18n.language, { dateStyle: "short" });

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
        <label className="field field-inline search-field">
          <span className="visually-hidden">{t("contacts.search")}</span>
          <input
            type="search"
            placeholder={t("contacts.searchPlaceholder")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </div>

      <section className="card">
        {contacts.isPending && <p className="muted">{t("common.loading")}</p>}
        {contacts.isError && <p className="alert alert-error">{t("errors.unknown")}</p>}
        {contacts.data && items.length === 0 && (
          <p className="muted">{term ? t("contacts.notFound") : t("contacts.empty")}</p>
        )}
        {items.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("contacts.fullName")}</th>
                  <th>{t("contacts.phone")}</th>
                  <th>{t("contacts.telegram")}</th>
                  <th>{t("contacts.channel")}</th>
                  <th>{t("contacts.added")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <Link to={`/app/clients/${c.id}`}>{c.full_name}</Link>
                      {c.full_name_latin && <div className="muted small">{c.full_name_latin}</div>}
                    </td>
                    <td className="nowrap mono">{formatPhone(c.phone)}</td>
                    <td className="muted">{c.telegram ? `@${c.telegram}` : ""}</td>
                    <td className="muted">{channelById.get(c.lead_channel_id ?? "")?.name}</td>
                    <td className="muted nowrap">{date.format(new Date(c.created_at))}</td>
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
    </div>
  );
}

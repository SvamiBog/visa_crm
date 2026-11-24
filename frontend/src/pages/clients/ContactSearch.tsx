import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { apiPost } from "../../api";
import { formatPhone, type ContactListItem } from "../../contacts";

const SHOWN = 8;

/** «Найти клиента»: an existing client by name, phone or Telegram, for a family case or a
 * submission group. Clients are not linked to each other (backlog 1.15.11), so a family
 * member is looked up here. The query goes in the request body, never in the URL. */
export function ContactSearch({
  exclude,
  onPick,
}: {
  // Already chosen, or the client the form is for.
  exclude: Set<string>;
  onPick: (contact: ContactListItem) => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const term = query.trim();
  const found = useQuery({
    queryKey: ["contacts", "search", term, "pick"],
    queryFn: () => apiPost<{ items: ContactListItem[] }>("/contacts/search", { query: term }),
    enabled: term.length >= 2,
  });
  const items = (found.data?.items ?? []).filter((c) => !exclude.has(c.id)).slice(0, SHOWN);

  return (
    <div className="contact-search">
      <label className="field">
        <span>{t("contacts.findContact")}</span>
        <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
      {found.data && items.length === 0 && <p className="muted small">{t("contacts.notFound")}</p>}
      {items.length > 0 && (
        <ul className="plain-list">
          {items.map((c) => (
            <li key={c.id}>
              <span>
                {c.full_name}
                {c.phone && <span className="muted"> · {formatPhone(c.phone)}</span>}
              </span>
              <button type="button" className="btn btn-sm" onClick={() => onPick(c)}>
                {t("contacts.pick")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

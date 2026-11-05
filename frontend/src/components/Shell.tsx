import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, matchPath, NavLink, useLocation, useNavigate } from "react-router-dom";
import { apiGet, apiPost } from "../api";
import { ME_KEY, useMe, type Role } from "../auth";
import type { SubscriptionState } from "../subscriptions";
import { Icon, type IconName } from "./Icon";
import { Logo } from "./Logo";

type Area = "admin" | "cabinet";

type Page = { key: string; to: string; roles?: Role[]; module?: string };

/** A page of the menu; `short` names it on the phone's bottom bar. */
type MenuItem = Page & { icon: IconName; short?: string };

/** The first group has no heading: the pages of everyday work. */
type MenuGroup = { key?: string; items: MenuItem[] };

// Items of a module show only if the user has that module; a group left empty hides.
const MENU: Record<Area, MenuGroup[]> = {
  admin: [
    {
      items: [
        { key: "nav.specialists", to: "/admin", icon: "clients" },
        { key: "nav.subscriptions", to: "/admin/subscriptions", icon: "wallet" },
        { key: "nav.reference", to: "/admin/reference", icon: "globe", short: "nav.referenceShort" },
        { key: "nav.audit", to: "/admin/audit", icon: "journal" },
      ],
    },
  ],
  cabinet: [
    {
      items: [
        { key: "nav.home", to: "/app", icon: "home" },
        { key: "nav.clients", to: "/app/clients", icon: "clients", module: "clients" },
        {
          key: "nav.queues",
          to: "/app/queues",
          icon: "queues",
          short: "nav.queuesShort",
          module: "cases",
        },
        { key: "nav.bookings", to: "/app/bookings", icon: "bookings", module: "cases" },
        { key: "nav.vault", to: "/app/vault", icon: "vault", module: "vault" },
      ],
    },
    {
      key: "nav.documents",
      items: [
        { key: "nav.checklists", to: "/app/checklists", icon: "checklists", module: "package_check" },
        {
          key: "nav.templates",
          to: "/app/templates",
          icon: "templates",
          module: "document_templates",
        },
        {
          key: "nav.stayCalculator",
          to: "/app/stay-calculator",
          icon: "calculator",
          module: "stay_calculator",
        },
      ],
    },
    {
      key: "nav.money",
      items: [
        // The balance covers every case, so it stays with the specialist (вопрос 17).
        {
          key: "nav.registrars",
          to: "/app/registrars",
          icon: "wallet",
          roles: ["specialist"],
          module: "finances",
        },
        { key: "nav.analytics", to: "/app/analytics", icon: "analytics", module: "analytics" },
      ],
    },
  ],
};

// Set up once and changed rarely, so they open from «Настройки», not from the menu.
const SETTINGS: Record<Area, Page[]> = {
  admin: [],
  cabinet: [
    { key: "nav.countries", to: "/app/countries", module: "country_rules" },
    { key: "nav.dictionaries", to: "/app/dictionaries", roles: ["specialist"] },
    { key: "nav.employees", to: "/app/employees", roles: ["specialist"] },
    { key: "nav.import", to: "/app/import", roles: ["specialist"], module: "clients" },
    { key: "nav.audit", to: "/app/audit", roles: ["specialist"] },
  ],
};

const ACCOUNT: Page = { key: "nav.account", to: "/settings" };

// The phone's bottom bar: the first pages of everyday work; the rest open with «Ещё».
const TABS = 4;

// The area's home page; every other page also covers its own pages below (a client's card).
const ROOTS = ["/app", "/admin"];
const exact = (to: string) => ROOTS.includes(to);
const isAt = (to: string, pathname: string) =>
  matchPath({ path: to, end: exact(to) }, pathname) !== null;

export function Shell({ area, children }: { area: Area; children: ReactNode }) {
  const { t } = useTranslation();
  const me = useMe();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const menuId = useId();
  const accountId = useId();
  const moreRef = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const settingsNavRef = useRef<HTMLElement>(null);
  // On a phone the menu opens from «Ещё» as a sheet above the bottom bar.
  const [menuOpen, setMenuOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () =>
      apiGet<{ modules: string[]; subscription: SubscriptionState | null }>("/workspace"),
    enabled: area === "cabinet",
  });
  // Only the specialist gets the state (the API leaves it out for employees).
  const subscription = workspace.data?.subscription;
  const overdue = subscription?.status === "overdue" ? subscription : null;
  // The items show once the modules are known, so the menu does not grow under the cursor.
  const ready = area === "admin" || !workspace.isPending;
  const visible = (page: Page) =>
    (!page.roles || (me.data && page.roles.includes(me.data.role))) &&
    (!page.module || workspace.data?.modules.includes(page.module));
  const groups = ready
    ? MENU[area]
        .map((group) => ({ ...group, items: group.items.filter(visible) }))
        .filter((group) => group.items.length > 0)
    : [];
  const tabs = groups[0]?.items.slice(0, TABS) ?? [];
  const settings = ready ? [...SETTINGS[area].filter(visible), ACCOUNT] : [ACCOUNT];
  // Known from the address alone, so a settings page is not laid out again when the
  // modules arrive.
  const inSettings = [...SETTINGS[area], ACCOUNT].some((page) => isAt(page.to, pathname));
  const submenu = ready && settings.length > 1;
  const logout = useMutation({
    mutationFn: () => apiPost<void>("/auth/logout"),
    onSettled: () => {
      queryClient.clear();
      queryClient.setQueryData(ME_KEY, null);
      navigate("/login", { replace: true });
    },
  });

  useEffect(() => {
    setMenuOpen(false);
    setAccountOpen(false);
  }, [pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      moreRef.current?.focus();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [menuOpen]);
  // On a phone the settings scroll sideways: the open one is brought into view.
  useEffect(() => {
    const nav = settingsNavRef.current;
    const active = nav?.querySelector<HTMLElement>(".active");
    if (nav && active) {
      nav.scrollLeft = active.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2;
    }
  }, [pathname, submenu]);

  // A link of the menu closes it, also when it leads to the page already shown.
  const closeOnLink = (event: MouseEvent<HTMLElement>) => {
    if (!(event.target instanceof Element && event.target.closest("a"))) return;
    setMenuOpen(false);
    setAccountOpen(false);
  };

  return (
    <div className="shell">
      <a
        href="#content"
        className="skip-link"
        onClick={(event) => {
          event.preventDefault();
          mainRef.current?.focus();
        }}
      >
        {t("nav.skipToContent")}
      </a>
      <nav
        id={menuId}
        className={menuOpen ? "sidebar open" : "sidebar"}
        aria-label={t("nav.label")}
        onClick={closeOnLink}
      >
        <div className="sidebar-bar">
          <Logo inverted />
        </div>
        <div className="sidebar-area">
          {t(area === "admin" ? "nav.adminArea" : "nav.cabinetArea")}
        </div>
        {groups.map((group, index) => {
          const headingId = `${menuId}-group-${index}`;
          return (
            <div
              key={group.key ?? index}
              className="nav-group"
              role={group.key ? "group" : undefined}
              aria-labelledby={group.key ? headingId : undefined}
            >
              {group.key && (
                <div id={headingId} className="nav-heading">
                  {t(group.key)}
                </div>
              )}
              {group.items.map((item) => (
                <NavLink
                  key={item.key}
                  to={item.to}
                  end={exact(item.to)}
                  className={tabs.includes(item) ? "nav-a on-tabs" : "nav-a"}
                >
                  <Icon name={item.icon} />
                  {t(item.key)}
                </NavLink>
              ))}
            </div>
          );
        })}
        <div className="sidebar-footer">
          {ready && (
            <Link
              to={settings[0].to}
              className={inSettings ? "nav-a active" : "nav-a"}
              aria-current={inSettings ? "true" : undefined}
            >
              <Icon name="settings" />
              {t("nav.settings")}
            </Link>
          )}
          <button
            type="button"
            className="account-toggle"
            aria-expanded={accountOpen}
            aria-controls={accountId}
            title={me.data?.email}
            onClick={() => setAccountOpen((open) => !open)}
          >
            <span className="account-email">{me.data?.email}</span>
            <Icon name="chevron" />
          </button>
          <div id={accountId} className="account-menu" hidden={!accountOpen}>
            <Link to={ACCOUNT.to} className="sidebar-link">
              {t(ACCOUNT.key)}
            </Link>
            <Link to="/change-password" className="sidebar-link">
              {t("auth.changePassword")}
            </Link>
            {me.data && !me.data.two_factor_enabled && (
              <Link to="/setup-two-factor" className="sidebar-link">
                {t("twoFactor.turnOn")}
              </Link>
            )}
            <button
              type="button"
              className="sidebar-link"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
            >
              {t("auth.logout")}
            </button>
          </div>
        </div>
      </nav>
      <main id="content" ref={mainRef} tabIndex={-1} className="content">
        {overdue && (
          <div className="alert alert-warn subscription-notice" role="status">
            {t("subscription.overdueBanner", { days: overdue.overdue_days })}{" "}
            <Link to="/settings">{t("subscription.overdueLink")}</Link>
          </div>
        )}
        {inSettings ? (
          <div className={submenu ? "settings-layout with-nav" : "settings-layout"}>
            {submenu && (
              <nav ref={settingsNavRef} className="settings-nav" aria-label={t("nav.settings")}>
                <div
                  className="settings-group"
                  role="group"
                  aria-labelledby={`${menuId}-settings`}
                >
                  <div id={`${menuId}-settings`} className="settings-heading">
                    {t("nav.workspaceSettings")}
                  </div>
                  {settings.slice(0, -1).map((page) => (
                    <NavLink key={page.key} to={page.to} className="settings-a">
                      {t(page.key)}
                    </NavLink>
                  ))}
                </div>
                <div className="settings-group settings-own">
                  <NavLink to={ACCOUNT.to} className="settings-a">
                    {t(ACCOUNT.key)}
                  </NavLink>
                </div>
              </nav>
            )}
            <div className="settings-body">{children}</div>
          </div>
        ) : (
          children
        )}
      </main>
      {menuOpen && (
        <div className="sheet-backdrop" aria-hidden="true" onClick={() => setMenuOpen(false)} />
      )}
      <nav className="tabbar" aria-label={t("nav.tabs")} onClick={closeOnLink}>
        {tabs.map((item) => (
          <NavLink key={item.key} to={item.to} end={exact(item.to)} className="bar-a">
            <Icon name={item.icon} />
            <span>{t(item.short ?? item.key)}</span>
          </NavLink>
        ))}
        {ready && (
          <button
            ref={moreRef}
            type="button"
            // A page of the sheet is shown: «Ещё» marks where it is.
            className={tabs.some((item) => isAt(item.to, pathname)) ? "bar-a" : "bar-a active"}
            aria-expanded={menuOpen}
            aria-controls={menuId}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <Icon name="more" />
            <span>{t("nav.more")}</span>
          </button>
        )}
      </nav>
    </div>
  );
}

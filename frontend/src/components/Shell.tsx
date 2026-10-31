import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { apiGet, apiPost } from "../api";
import { ME_KEY, useMe, type Role } from "../auth";
import type { SubscriptionState } from "../subscriptions";
import { Logo } from "./Logo";

type Area = "admin" | "cabinet";

type MenuItem = { key: string; to?: string; roles?: Role[]; module?: string };

// Items not built yet are shown as "soon" so the cabinet reads as the planned product;
// items of a module show only if the user has that module.
const MENU: Record<Area, MenuItem[]> = {
  admin: [
    { key: "nav.specialists", to: "/admin" },
    { key: "nav.subscriptions", to: "/admin/subscriptions" },
    { key: "nav.reference", to: "/admin/reference" },
    { key: "nav.audit", to: "/admin/audit" },
  ],
  cabinet: [
    { key: "nav.home", to: "/app" },
    { key: "nav.clients", to: "/app/clients", module: "clients" },
    { key: "nav.queues", to: "/app/queues", module: "cases" },
    { key: "nav.bookings", to: "/app/bookings", module: "cases" },
    // The balance covers every case, so it stays with the specialist (вопрос 17).
    { key: "nav.registrars", to: "/app/registrars", roles: ["specialist"], module: "finances" },
    { key: "nav.vault", to: "/app/vault", module: "vault" },
    { key: "nav.analytics", to: "/app/analytics", module: "analytics" },
    { key: "nav.countries", to: "/app/countries", module: "country_rules" },
    { key: "nav.templates", to: "/app/templates", module: "document_templates" },
    { key: "nav.checklists", to: "/app/checklists", module: "package_check" },
    { key: "nav.stayCalculator", to: "/app/stay-calculator", module: "stay_calculator" },
    { key: "nav.import", to: "/app/import", roles: ["specialist"], module: "clients" },
    { key: "nav.dictionaries", to: "/app/dictionaries", roles: ["specialist"] },
    { key: "nav.employees", to: "/app/employees", roles: ["specialist"] },
    { key: "nav.audit", to: "/app/audit", roles: ["specialist"] },
  ],
};

export function Shell({ area, children }: { area: Area; children: ReactNode }) {
  const { t } = useTranslation();
  const me = useMe();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const menuId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  // On a phone the menu hides behind «Меню» so a page starts on the first screen.
  const [menuOpen, setMenuOpen] = useState(false);
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
  const visible = (item: MenuItem) =>
    (!item.roles || (me.data && item.roles.includes(me.data.role))) &&
    (!item.module || workspace.data?.modules.includes(item.module));
  const logout = useMutation({
    mutationFn: () => apiPost<void>("/auth/logout"),
    onSettled: () => {
      queryClient.clear();
      queryClient.setQueryData(ME_KEY, null);
      navigate("/login", { replace: true });
    },
  });

  useEffect(() => setMenuOpen(false), [pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      toggleRef.current?.focus();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [menuOpen]);

  // A link of the open menu closes it, also when it leads to the page already shown.
  const closeOnLink = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target instanceof Element && event.target.closest("a")) setMenuOpen(false);
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
      <nav className={menuOpen ? "sidebar open" : "sidebar"} aria-label={t("nav.label")}>
        <div className="sidebar-bar">
          <Logo inverted />
          <button
            ref={toggleRef}
            type="button"
            className="menu-toggle"
            aria-expanded={menuOpen}
            aria-controls={menuId}
            onClick={() => setMenuOpen((open) => !open)}
          >
            {t("nav.menu")}
          </button>
        </div>
        <div id={menuId} className="sidebar-menu" onClick={closeOnLink}>
          <div className="sidebar-area">
            {t(area === "admin" ? "nav.adminArea" : "nav.cabinetArea")}
          </div>
          {ready &&
            MENU[area].filter(visible).map((item) =>
              item.to ? (
                <NavLink key={item.key} to={item.to} end className="nav-a">
                  {t(item.key)}
                </NavLink>
              ) : (
                <span key={item.key} className="nav-a nav-soon" aria-disabled="true">
                  {t(item.key)}
                  <span className="soon">{t("nav.soon")}</span>
                </span>
              ),
            )}
          <div className="sidebar-footer">
            <div className="sidebar-email" title={me.data?.email}>
              {me.data?.email}
            </div>
            <Link to="/settings" className="sidebar-link">
              {t("nav.settings")}
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
        {children}
      </main>
    </div>
  );
}

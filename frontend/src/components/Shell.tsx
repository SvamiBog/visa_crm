import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { apiGet, apiPost } from "../api";
import { ME_KEY, useMe, type Role } from "../auth";
import { Logo } from "./Logo";

type Area = "admin" | "cabinet";

type MenuItem = { key: string; to?: string; roles?: Role[]; module?: string };

// Items not built yet are shown as "soon" so the cabinet reads as the planned product;
// items of a module show only if the user has that module.
const MENU: Record<Area, MenuItem[]> = {
  admin: [
    { key: "nav.specialists", to: "/admin" },
    { key: "nav.reference", to: "/admin/reference" },
    { key: "nav.audit", to: "/admin/audit" },
  ],
  cabinet: [
    { key: "nav.home", to: "/app" },
    { key: "nav.clients", to: "/app/clients", module: "clients" },
    { key: "nav.queues", to: "/app/queues", module: "cases" },
    { key: "nav.bookings", to: "/app/bookings", module: "cases" },
    { key: "nav.registrars", to: "/app/registrars", module: "finances" },
    { key: "nav.vault", to: "/app/vault", module: "vault" },
    { key: "nav.analytics", module: "analytics" },
    { key: "nav.countries", to: "/app/countries", module: "country_rules" },
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
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<{ modules: string[] }>("/workspace"),
    enabled: area === "cabinet",
  });
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

  return (
    <div className="shell">
      <nav className="sidebar" aria-label={t("nav.label")}>
        <Logo inverted />
        <div className="sidebar-area">
          {t(area === "admin" ? "nav.adminArea" : "nav.cabinetArea")}
        </div>
        {MENU[area].filter(visible).map((item) =>
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
      </nav>
      <main className="content">{children}</main>
    </div>
  );
}

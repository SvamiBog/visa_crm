import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { apiPost } from "../api";
import { ME_KEY, useMe } from "../auth";
import { LanguageSwitch } from "./LanguageSwitch";
import { Logo } from "./Logo";

type Area = "admin" | "cabinet";

// Menu items not built yet are shown as "soon" so the cabinet reads as the planned product.
const MENU: Record<Area, { key: string; to?: string }[]> = {
  admin: [{ key: "nav.specialists", to: "/admin" }],
  cabinet: [
    { key: "nav.home", to: "/app" },
    { key: "nav.clients" },
    { key: "nav.queues" },
    { key: "nav.analytics" },
  ],
};

export function Shell({ area, children }: { area: Area; children: ReactNode }) {
  const { t } = useTranslation();
  const me = useMe();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
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
        <div className="sidebar-area">{t(area === "admin" ? "nav.adminArea" : "nav.cabinetArea")}</div>
        {MENU[area].map((item) =>
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
          <Link to="/change-password" className="sidebar-link">
            {t("auth.changePassword")}
          </Link>
          <LanguageSwitch className="sidebar-link" />
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

import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { apiGet } from "./api";
import { homePath, useMe, type Role } from "./auth";
import { Shell } from "./components/Shell";
import { SpecialistsPage } from "./pages/admin/SpecialistsPage";
import { AuditPage } from "./pages/AuditPage";
import { HomePage } from "./pages/cabinet/HomePage";
import { ChangePasswordPage } from "./pages/ChangePasswordPage";
import { LoginPage } from "./pages/LoginPage";
import { TwoFactorSetupPage } from "./pages/TwoFactorSetupPage";

function Loading() {
  const { t } = useTranslation();
  return <div className="center-screen muted">{t("common.loading")}</div>;
}

/** Lets a page through only for a logged-in user with one of the given roles. */
function RequireUser({ roles, children }: { roles?: Role[]; children: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) return <Loading />;
  if (!me.data) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  // Unfinished account setup: only its own page opens (the API enforces the same).
  const setupPath = homePath(me.data);
  const inSetup = me.data.must_change_password || me.data.must_set_up_two_factor;
  if (inSetup && location.pathname !== setupPath) return <Navigate to={setupPath} replace />;
  if (roles && !roles.includes(me.data.role)) return <Navigate to={homePath(me.data)} replace />;
  return children;
}

function RootRedirect() {
  const me = useMe();
  if (me.isPending) return <Loading />;
  return <Navigate to={me.data ? homePath(me.data) : "/login"} replace />;
}

/** Times in the workspace's time zone (Europe/Minsk by default), as everywhere in the cabinet. */
function WorkspaceAudit() {
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<{ timezone: string }>("/workspace"),
  });
  return <AuditPage scope="workspace" timeZone={workspace.data?.timezone} />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/change-password"
        element={
          <RequireUser>
            <ChangePasswordPage />
          </RequireUser>
        }
      />
      <Route
        path="/setup-two-factor"
        element={
          <RequireUser>
            <TwoFactorSetupPage />
          </RequireUser>
        }
      />
      <Route
        path="/admin"
        element={
          <RequireUser roles={["admin"]}>
            <Shell area="admin">
              <SpecialistsPage />
            </Shell>
          </RequireUser>
        }
      />
      <Route
        path="/admin/audit"
        element={
          <RequireUser roles={["admin"]}>
            <Shell area="admin">
              <AuditPage scope="admin" />
            </Shell>
          </RequireUser>
        }
      />
      <Route
        path="/app/audit"
        element={
          <RequireUser roles={["specialist"]}>
            <Shell area="cabinet">
              <WorkspaceAudit />
            </Shell>
          </RequireUser>
        }
      />
      <Route
        path="/app"
        element={
          <RequireUser roles={["specialist", "employee"]}>
            <Shell area="cabinet">
              <HomePage />
            </Shell>
          </RequireUser>
        }
      />
      <Route path="*" element={<RootRedirect />} />
    </Routes>
  );
}

import { useQuery } from "@tanstack/react-query";
import { Suspense, useEffect, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { apiGet } from "./api";
import { homePath, useMe, type Role } from "./auth";
import { lazyPage } from "./components/LazyPage";
import { Shell } from "./components/Shell";
import { BookingsPage } from "./pages/bookings/BookingsPage";
import { HomePage } from "./pages/cabinet/HomePage";
import { ClientsPage } from "./pages/clients/ClientsPage";
import { ContactPage } from "./pages/clients/ContactPage";
import { ChangePasswordPage } from "./pages/ChangePasswordPage";
import { LoginPage } from "./pages/LoginPage";
import { QueuesPage } from "./pages/queues/QueuesPage";
import { TwoFactorSetupPage } from "./pages/TwoFactorSetupPage";
import { VaultPage } from "./pages/vault/VaultPage";
import { applyTheme } from "./theme";

// The pages of everyday work (the first group of the menu) and the login pages come with the
// app; the rest are fetched the first time they open, so the first visit loads less.
const AnalyticsPage = lazyPage(() => import("./pages/analytics/AnalyticsPage"), "AnalyticsPage");
const AuditPage = lazyPage(() => import("./pages/AuditPage"), "AuditPage");
const ChecklistsPage = lazyPage(
  () => import("./pages/checklists/ChecklistsPage"),
  "ChecklistsPage",
);
const CountriesPage = lazyPage(() => import("./pages/cabinet/CountriesPage"), "CountriesPage");
const DictionariesPage = lazyPage(
  () => import("./pages/cabinet/DictionariesPage"),
  "DictionariesPage",
);
const EmployeesPage = lazyPage(() => import("./pages/cabinet/EmployeesPage"), "EmployeesPage");
const ImportPage = lazyPage(() => import("./pages/import/ImportPage"), "ImportPage");
const ReferencePage = lazyPage(() => import("./pages/admin/ReferencePage"), "ReferencePage");
const RegistrarsPage = lazyPage(
  () => import("./pages/registrars/RegistrarsPage"),
  "RegistrarsPage",
);
const SettingsPage = lazyPage(() => import("./pages/SettingsPage"), "SettingsPage");
const SpecialistsPage = lazyPage(() => import("./pages/admin/SpecialistsPage"), "SpecialistsPage");
const StayCalculatorPage = lazyPage(
  () => import("./pages/stay/StayCalculatorPage"),
  "StayCalculatorPage",
);
const SubscriptionsPage = lazyPage(
  () => import("./pages/admin/SubscriptionsPage"),
  "SubscriptionsPage",
);
const TemplatesPage = lazyPage(() => import("./pages/templates/TemplatesPage"), "TemplatesPage");

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
  // A temporary password: only the page to replace it opens (the API enforces the same).
  // Two-factor login is optional and set up from Settings.
  const setupPath = homePath(me.data);
  if (me.data.must_change_password && location.pathname !== setupPath) {
    return <Navigate to={setupPath} replace />;
  }
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

/** Settings open inside the user's own area: the admin panel or the cabinet. */
function Settings() {
  const me = useMe();
  return (
    <Shell area={me.data?.role === "admin" ? "admin" : "cabinet"}>
      <SettingsPage />
    </Shell>
  );
}

/** On the staging stand: a strip on every page, so nobody takes it for the real system. The
 * server marks the page itself (backend/app/main.py), so the strip is drawn with the first
 * paint: waiting for /api/health, it pushed every page down once it came. */
function EnvBanner() {
  const { t } = useTranslation();
  if (document.documentElement.dataset.env !== "staging") return null;
  return (
    <div className="env-banner" role="note">
      {t("env.staging")}
    </div>
  );
}

export default function App() {
  const me = useMe();
  const theme = me.data?.ui_theme;
  // The account's style wins over the one remembered on this device.
  useEffect(() => {
    if (theme) applyTheme(theme);
  }, [theme]);

  return (
    // Lazy pages wait in the Shell's page area; this catches only one rendered outside it.
    <Suspense fallback={<Loading />}>
      <EnvBanner />
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
          path="/admin/subscriptions"
          element={
            <RequireUser roles={["admin"]}>
              <Shell area="admin">
                <SubscriptionsPage />
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
          path="/admin/reference"
          element={
            <RequireUser roles={["admin"]}>
              <Shell area="admin">
                <ReferencePage />
              </Shell>
            </RequireUser>
          }
        />
        <Route
          path="/app/countries"
          element={
            <RequireUser roles={["specialist", "employee"]}>
              <Shell area="cabinet">
                <CountriesPage />
              </Shell>
            </RequireUser>
          }
        />
        <Route
          path="/app/templates"
          element={
            <RequireUser roles={["specialist", "employee"]}>
              <Shell area="cabinet">
                <TemplatesPage />
              </Shell>
            </RequireUser>
          }
        />
        <Route
          path="/app/checklists"
          element={
            <RequireUser roles={["specialist", "employee"]}>
              <Shell area="cabinet">
                <ChecklistsPage />
              </Shell>
            </RequireUser>
          }
        />
        <Route
          path="/app/clients"
          element={
            <RequireUser roles={["specialist", "employee"]}>
              <Shell area="cabinet">
                <ClientsPage />
              </Shell>
            </RequireUser>
          }
        />
        <Route
          path="/app/clients/:id"
          element={
            <RequireUser roles={["specialist", "employee"]}>
              <Shell area="cabinet">
                <ContactPage />
              </Shell>
            </RequireUser>
          }
        />
        <Route
          path="/app/queues"
          element={
            <RequireUser roles={["specialist", "employee"]}>
              <Shell area="cabinet">
                <QueuesPage />
              </Shell>
            </RequireUser>
          }
        />
        <Route
          path="/app/analytics"
          element={
            <RequireUser roles={["specialist", "employee"]}>
              <Shell area="cabinet">
                <AnalyticsPage />
              </Shell>
            </RequireUser>
          }
        />
        <Route
          path="/app/bookings"
          element={
            <RequireUser roles={["specialist", "employee"]}>
              <Shell area="cabinet">
                <BookingsPage />
              </Shell>
            </RequireUser>
          }
        />
        {["/app/registrars", "/app/registrars/:id"].map((path) => (
          <Route
            key={path}
            path={path}
            element={
              <RequireUser roles={["specialist"]}>
                <Shell area="cabinet">
                  <RegistrarsPage />
                </Shell>
              </RequireUser>
            }
          />
        ))}
        <Route
          path="/app/vault"
          element={
            <RequireUser roles={["specialist", "employee"]}>
              <Shell area="cabinet">
                <VaultPage />
              </Shell>
            </RequireUser>
          }
        />
        <Route
          path="/app/stay-calculator"
          element={
            <RequireUser roles={["specialist", "employee"]}>
              <Shell area="cabinet">
                <StayCalculatorPage />
              </Shell>
            </RequireUser>
          }
        />
        <Route
          path="/app/import"
          element={
            <RequireUser roles={["specialist"]}>
              <Shell area="cabinet">
                <ImportPage />
              </Shell>
            </RequireUser>
          }
        />
        <Route
          path="/app/dictionaries"
          element={
            <RequireUser roles={["specialist"]}>
              <Shell area="cabinet">
                <DictionariesPage />
              </Shell>
            </RequireUser>
          }
        />
        <Route
          path="/app/employees"
          element={
            <RequireUser roles={["specialist"]}>
              <Shell area="cabinet">
                <EmployeesPage />
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
        <Route
          path="/settings"
          element={
            <RequireUser>
              <Settings />
            </RequireUser>
          }
        />
        <Route path="*" element={<RootRedirect />} />
      </Routes>
    </Suspense>
  );
}

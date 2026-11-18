import {
  Component,
  lazy,
  Suspense,
  type ComponentType,
  type LazyExoticComponent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";

/** The code of a page could not be fetched: no connection, or a fresh build did not help. */
class PageLoadFailed extends Error {}

// A deploy replaces the built files, so a tab opened before it cannot fetch the pages it has
// not opened yet. One reload picks up the new build; another failure soon after is not
// retried (the connection or the server is down), and the page says so instead.
const RELOADED_AT = "visacrm.reloadedForUpdate";
const RELOAD_PAUSE_MS = 60_000;

function reloadForNewBuild(): boolean {
  if (!navigator.onLine) return false;
  try {
    if (Date.now() - Number(sessionStorage.getItem(RELOADED_AT)) < RELOAD_PAUSE_MS) return false;
    sessionStorage.setItem(RELOADED_AT, String(Date.now()));
  } catch {
    return false; // blocked storage could not stop a reload loop, so none is started
  }
  window.location.reload();
  return true;
}

/**
 * A rarely used page whose code is fetched the first time it opens, so the first visit loads
 * only the pages of everyday work. It renders inside `PageArea` (the `Shell` has one).
 */
export function lazyPage<K extends string, P extends object>(
  load: () => Promise<Record<K, ComponentType<P>>>,
  name: K,
): LazyExoticComponent<ComponentType<P>> {
  return lazy(() =>
    load().then(
      (module) => ({ default: module[name] }),
      (error: unknown) => {
        // The loading line stays on screen until the page reloads.
        if (reloadForNewBuild()) return new Promise<never>(() => {});
        throw new PageLoadFailed("page code not loaded", { cause: error });
      },
    ),
  );
}

type BoundaryProps = { resetKey: string; fallback: ReactNode; children: ReactNode };
type BoundaryState = { error: unknown; resetKey: string };

/** Shows the connection error when a page's code could not be fetched; other errors pass on. */
class PageLoadBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: unknown): Partial<BoundaryState> {
    return { error };
  }

  static getDerivedStateFromProps(
    props: BoundaryProps,
    state: BoundaryState,
  ): Partial<BoundaryState> | null {
    // Another page opened: show it, not the error of the previous one.
    return props.resetKey === state.resetKey ? null : { error: null, resetKey: props.resetKey };
  }

  render(): ReactNode {
    if (this.state.error === null) return this.props.children;
    if (!(this.state.error instanceof PageLoadFailed)) throw this.state.error;
    return this.props.fallback;
  }
}

/**
 * Where a page renders: a quiet loading line while a lazy page's code is fetched, in the
 * page's own place so nothing around it moves; the connection error if the fetch failed.
 */
export function PageArea({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  return (
    <PageLoadBoundary
      resetKey={pathname}
      fallback={
        <div className="page">
          <p className="alert alert-error" role="alert">
            {t("errors.network")}
          </p>
        </div>
      }
    >
      <Suspense
        fallback={
          <div className="page">
            <p className="muted page-loading">{t("common.loading")}</p>
          </div>
        }
      >
        {children}
      </Suspense>
    </PageLoadBoundary>
  );
}

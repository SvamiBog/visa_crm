import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { ApiError, apiGet } from "./api";
import type { UiTheme } from "./theme";

export type Role = "admin" | "specialist" | "employee";

export type Me = {
  id: string;
  email: string;
  role: Role;
  must_change_password: boolean;
  two_factor_enabled: boolean;
  tenant_id: string | null;
  ui_theme: UiTheme;
};

export const ME_KEY = ["me"] as const;

/** Drops everything cached for the account that was signed in (security audit N14). The
 * query keys name no account, so whoever signs in next in this tab would be shown the last
 * one's client search, the rows it found and the cards it opened. The entry of the user
 * stays: the screens watching it learn who comes next, and its own request is not cut off
 * (queryClient.clear() would leave them watching a removed entry). */
export function forgetAccount(queryClient: QueryClient): void {
  queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== ME_KEY[0] });
  queryClient.getMutationCache().clear();
}

async function fetchMe(): Promise<Me | null> {
  try {
    return await apiGet<Me>("/auth/me");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

/** The logged-in user, or null when there is no valid session. */
export function useMe() {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ME_KEY,
    queryFn: async () => {
      const before = queryClient.getQueryData<Me | null>(ME_KEY);
      const me = await fetchMe();
      // The session ended (signed out in another tab, a new password, a blocked employee)
      // or another account signed in there: nothing cached was fetched for this one.
      if (me === null || (before && before.id !== me.id)) forgetAccount(queryClient);
      return me;
    },
    staleTime: 60_000,
    retry: false,
  });
}

/** Where a user lands after login. */
export function homePath(me: Me): string {
  if (me.must_change_password) return "/change-password";
  return me.role === "admin" ? "/admin" : "/app";
}

import { useQuery } from "@tanstack/react-query";
import { ApiError, apiGet } from "./api";
import type { UiTheme } from "./theme";

export type Role = "admin" | "specialist" | "employee";

export type Me = {
  id: string;
  email: string;
  role: Role;
  must_change_password: boolean;
  two_factor_enabled: boolean;
  must_set_up_two_factor: boolean;
  tenant_id: string | null;
  ui_theme: UiTheme;
};

export const ME_KEY = ["me"] as const;

/** The logged-in user, or null when there is no valid session. */
export function useMe() {
  return useQuery({
    queryKey: ME_KEY,
    queryFn: async () => {
      try {
        return await apiGet<Me>("/auth/me");
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 60_000,
    retry: false,
  });
}

/** Where a user lands after login. */
export function homePath(me: Me): string {
  if (me.must_change_password) return "/change-password";
  if (me.must_set_up_two_factor) return "/setup-two-factor";
  return me.role === "admin" ? "/admin" : "/app";
}

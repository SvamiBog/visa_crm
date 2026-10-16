import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./api";

// The current workspace: its modules decide which blocks a user sees (the API checks
// the same on every request).

export function useWorkspace() {
  return useQuery({
    queryKey: ["workspace"],
    queryFn: () => apiGet<{ modules: string[]; timezone: string }>("/workspace"),
  });
}

export function useHasModule(key: string): boolean {
  return useWorkspace().data?.modules.includes(key) ?? false;
}

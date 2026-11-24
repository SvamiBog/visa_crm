import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDelete, apiDownload, apiGet, apiPost, apiPut } from "./api";
import type { PackageFile } from "./package";

// The client portal (backlog 2.9): in the case — the link, the code and the files
// at checklist items (backend/app/api/portal.py); for the client — the page /u/<token>
// (backend/app/api/portal_public.py). Settings — the specialist's details as the data
// controller, named in the notice the client sees.

export type CodeChannel = "email" | "specialist" | "telegram";
export type ConsentGivenBy = "applicant" | "representative";
export type ReturnReason = "unreadable" | "need_fresh" | "other";
export const RETURN_REASONS: ReturnReason[] = ["unreadable", "need_fresh", "other"];

// Files the portal takes (the server checks the type by content): PDF and photos to 20 MB.
export const MAX_PORTAL_FILE_BYTES = 20 * 1024 * 1024;
export const PORTAL_FILE_TYPES = [
  ".pdf,.jpg,.jpeg,.png,.webp,.heic,.heif",
  "application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif",
].join(",");

export type PortalConsent = {
  given_at: string;
  notice_version: string;
  special_categories: boolean;
  given_by: ConsentGivenBy;
  representative_name: string | null;
  code_channel: CodeChannel;
};

export type Portal = {
  open: boolean;
  token: string | null;
  created_at: string | null;
  // The link's code: no expiry, it stays until «Новый код» replaces it.
  code: string | null;
  code_channel: CodeChannel | null;
  // Five wrong tries in a row: the client needs a new code.
  code_blocked: boolean;
  has_email: boolean;
  controller_ready: boolean;
  consent: PortalConsent | null;
};

export type IssuedCode = { channel: CodeChannel; code: string };

export function portalUrl(token: string): string {
  return `${window.location.origin}/u/${token}`;
}

const portalKey = (caseId: string) => ["cases", caseId, "portal"] as const;
const packageKey = (caseId: string) => ["cases", caseId, "package"] as const;

export function usePortal(caseId: string) {
  return useQuery({
    queryKey: portalKey(caseId),
    queryFn: () => apiGet<Portal>(`/cases/${caseId}/portal`),
  });
}

export function useRevokeLink(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost<Portal>(`/cases/${caseId}/portal/revoke`),
    onSuccess: (data) => queryClient.setQueryData(portalKey(caseId), data),
  });
}

export function useNewCode(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (channel: CodeChannel) =>
      apiPost<IssuedCode>(`/cases/${caseId}/portal/code`, { channel }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: portalKey(caseId) }),
  });
}

/** Accept, return, delete, upload: each changes the item and the package's readiness. */
function usePackageChange<T>(caseId: string, fn: (arg: T) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: packageKey(caseId) });
      void queryClient.invalidateQueries({ queryKey: ["reminders"] });
      void queryClient.invalidateQueries({ queryKey: ["home"] });
    },
  });
}

export function useAcceptFile(caseId: string) {
  return usePackageChange(caseId, (a: { file: PackageFile; document_date: string | null }) =>
    apiPost<PackageFile>(`/cases/${caseId}/package-files/${a.file.id}/accept`, {
      document_date: a.document_date,
    }),
  );
}

export function useReturnFile(caseId: string) {
  return usePackageChange(
    caseId,
    (a: { file: PackageFile; reason: ReturnReason; note: string | null }) =>
      apiPost<PackageFile>(`/cases/${caseId}/package-files/${a.file.id}/return`, {
        reason: a.reason,
        note: a.note,
      }),
  );
}

export function useDeleteFile(caseId: string) {
  return usePackageChange(caseId, (file: PackageFile) =>
    apiDelete<void>(`/cases/${caseId}/package-files/${file.id}`),
  );
}

export function useAddFile(caseId: string, itemId: string) {
  return usePackageChange(
    caseId,
    (a: { file_name: string; content: string; document_date: string | null }) =>
      apiPost<PackageFile>(`/cases/${caseId}/package/${itemId}/files`, a),
  );
}

export function downloadFile(caseId: string, file: PackageFile): Promise<void> {
  return apiDownload(`/cases/${caseId}/package-files/${file.id}`, file.file_name);
}

// --- The specialist as the data controller («Настройки») ---------------------------

export type Controller = {
  name: string | null;
  address: string | null;
  email: string | null;
  supervisory_authority: string | null;
  supervisory_authority_default: string;
};

const CONTROLLER_KEY = ["workspace", "controller"] as const;

export function useController(enabled: boolean) {
  return useQuery({
    queryKey: CONTROLLER_KEY,
    queryFn: () => apiGet<Controller>("/workspace/controller"),
    enabled,
  });
}

export function useSaveController() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      address: string;
      email: string;
      supervisory_authority: string | null;
    }) => apiPut<Controller>("/workspace/controller", body),
    onSuccess: (data) => {
      queryClient.setQueryData(CONTROLLER_KEY, data);
      void queryClient.invalidateQueries({ queryKey: ["cases"] });
    },
  });
}

// --- The client's page /u/<token> ----------------------------------------------------

export type ClientItemStatus = "to_upload" | "pending" | "accepted" | "returned";

export type ClientFile = {
  id: string;
  file_name: string;
  status: "pending" | "accepted" | "returned";
  return_reason: ReturnReason | null;
  return_note: string | null;
  created_at: string;
  deletable: boolean;
};

export type ClientItem = {
  id: string;
  name: string;
  note: string | null;
  required: boolean;
  status: ClientItemStatus;
  files: ClientFile[];
};

export type ClientPage = {
  country: { code: string; name_ru: string; name_en: string };
  ready: boolean;
  consent: {
    needed: boolean;
    notice: { id: string; version: string; language: string; body: string } | null;
    withdraw_email: string | null;
  };
  items: ClientItem[];
};

const pageKey = (token: string, lang: string) => ["portal", token, lang] as const;

export function useClientPage(token: string, lang: string, enabled: boolean) {
  return useQuery({
    queryKey: pageKey(token, lang),
    queryFn: () => apiGet<ClientPage>(`/portal/${token}?lang=${lang}`),
    retry: false,
    enabled,
  });
}

function useClientChange<T>(token: string, fn: (arg: T) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["portal", token] }),
  });
}

export function useAskCode(token: string) {
  return useMutation({
    mutationFn: () => apiPost<{ channel: CodeChannel }>(`/portal/${token}/code`),
  });
}

export function useClientLogin(token: string) {
  return useClientChange(token, (code: string) => apiPost(`/portal/${token}/login`, { code }));
}

export function useGiveConsent(token: string) {
  return useClientChange(
    token,
    (body: {
      notice_id: string;
      special_categories: boolean;
      given_by: ConsentGivenBy;
      representative_name: string | null;
    }) => apiPost(`/portal/${token}/consent`, body),
  );
}

export function useClientUpload(token: string, itemId: string) {
  return useClientChange(token, (body: { file_name: string; content: string }) =>
    apiPost<ClientFile>(`/portal/${token}/items/${itemId}/files`, body),
  );
}

export function useClientDelete(token: string) {
  return useClientChange(token, (fileId: string) =>
    apiDelete<void>(`/portal/${token}/files/${fileId}`),
  );
}

export function useClientLogout(token: string) {
  return useClientChange(token, () => apiPost(`/portal/${token}/logout`));
}

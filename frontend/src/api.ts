// Thin fetch wrapper: same-origin requests, cookies included (session auth).
// Errors carry the API's machine-readable code (FastAPI "detail"), shown via t(`errors.${code}`).
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "include",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let code = "unknown";
    try {
      const data = (await response.json()) as { detail?: unknown };
      if (typeof data.detail === "string") code = data.detail;
      else if (response.status === 422) code = "validation";
    } catch {
      // not JSON: keep "unknown"
    }
    throw new ApiError(response.status, code);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const apiGet = <T>(path: string) => request<T>("GET", path);
export const apiPost = <T>(path: string, body?: unknown) => request<T>("POST", path, body);
export const apiPatch = <T>(path: string, body: unknown) => request<T>("PATCH", path, body);

export function errorCode(error: unknown): string {
  return error instanceof ApiError ? error.code : "network";
}

import { useSyncExternalStore } from "react";

// «Удалено · Отменить» (decisions.md → «Подтверждение и отмена удаления»): a small record
// is not deleted at once. Its row stays struck through and the deletion is sent when the
// time is up, so an undo has nothing to restore on the server and the log records only
// deletions that happened. The deletion goes at once when the page is hidden or closed
// (the request is sent with keepalive, see api.ts) and before logging out: what can be
// lost is a deletion, never the data.

/** How long «Отменить» is offered; the time stands while the pointer or focus is on it. */
export const UNDO_MS = 7000;

export type Deferred = {
  /** What is deleted, the same in its row: the row shows itself struck through. */
  key: string;
  /** Said in the message, already translated: «Заметка удалена». */
  message: string;
  /** Sends the deletion; absent when the change lives in the browser only. */
  commit?: () => Promise<unknown>;
  /** Puts back what the browser took away at once. */
  restore?: () => void;
};

export type UndoToast =
  | { kind: "pending"; message: string }
  | { kind: "failed"; error: unknown };

type Snapshot = { toast: UndoToast | null; deleting: ReadonlySet<string> };

let snapshot: Snapshot = { toast: null, deleting: new Set() };
let pending: Deferred | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
let remaining = 0;
let startedAt = 0;
const listeners = new Set<() => void>();

function publish(next: Partial<Snapshot>) {
  snapshot = { ...snapshot, ...next };
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function withKey(key: string, on: boolean): ReadonlySet<string> {
  const deleting = new Set(snapshot.deleting);
  if (on) deleting.add(key);
  else deleting.delete(key);
  return deleting;
}

function startTimer() {
  clearTimeout(timer);
  startedAt = Date.now();
  timer = setTimeout(() => void flushPending(), remaining);
}

/** Starts the wait; a deletion still waiting is sent now. */
export function deferDelete(item: Deferred): void {
  void flushPending();
  pending = item;
  remaining = UNDO_MS;
  publish({
    toast: { kind: "pending", message: item.message },
    deleting: withKey(item.key, true),
  });
  startTimer();
}

export function pauseUndo(): void {
  if (!pending || timer === undefined) return;
  clearTimeout(timer);
  timer = undefined;
  remaining = Math.max(0, remaining - (Date.now() - startedAt));
}

export function resumeUndo(): void {
  if (!pending || timer !== undefined) return;
  startTimer();
}

export function undoPending(): void {
  if (!pending) return;
  clearTimeout(timer);
  timer = undefined;
  const item = pending;
  pending = null;
  item.restore?.();
  publish({ toast: null, deleting: withKey(item.key, false) });
}

/** Sends the waiting deletion now — when its time is up, the page goes away, the user logs
 * out or deletes the next thing. With a key, only if that is what waits (its page closes).
 * Never throws: a refusal is shown in the message and the row comes back. */
export async function flushPending(key?: string): Promise<void> {
  if (!pending || (key !== undefined && pending.key !== key)) return;
  clearTimeout(timer);
  timer = undefined;
  const item = pending;
  pending = null;
  if (snapshot.toast?.kind === "pending") publish({ toast: null });
  try {
    await item.commit?.();
    publish({ deleting: withKey(item.key, false) });
  } catch (error) {
    item.restore?.();
    publish({
      toast: { kind: "failed", error },
      deleting: withKey(item.key, false),
    });
  }
}

export function dismissUndo(): void {
  if (snapshot.toast?.kind === "failed") publish({ toast: null });
}

export function useUndoToast(): UndoToast | null {
  return useSyncExternalStore(subscribe, () => snapshot.toast);
}

/** The keys of records that wait for their deletion or are being deleted. */
export function useDeleting(): ReadonlySet<string> {
  return useSyncExternalStore(subscribe, () => snapshot.deleting);
}

if (typeof window !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") void flushPending();
  });
  window.addEventListener("pagehide", () => void flushPending());
}

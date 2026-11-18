import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";

/** A question before a heavy action, in the site's own window instead of the browser's
 * (decisions.md → «Подтверждение и отмена удаления»). */
export type ConfirmOptions = {
  /** The whole question, already translated. */
  message: string;
  /** The button that does it, named for the action: «Удалить», «Выключить». */
  action: string;
  /** Red for what deletes, switches off or blocks; blue for what only guards a slip. */
  tone?: "danger" | "neutral";
  /** The button that refuses; «Отмена» unless that reads like the action itself. */
  cancel?: string;
};

type Ask = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<Ask>(() => Promise.resolve(false));

/** `await confirm({...})` is true only when the action button was pressed. */
export function useConfirm(): Ask {
  return useContext(ConfirmContext);
}

type Request = ConfirmOptions & { resolve: (yes: boolean) => void };

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<Request | null>(null);
  const current = useRef<Request | null>(null);

  const ask = useCallback<Ask>(
    (options) =>
      new Promise<boolean>((resolve) => {
        // A second question replaces an open one, which counts as refused.
        current.current?.resolve(false);
        current.current = { ...options, resolve };
        setRequest(current.current);
      }),
    [],
  );
  const answer = useCallback((yes: boolean) => {
    current.current?.resolve(yes);
    current.current = null;
    setRequest(null);
  }, []);

  return (
    <ConfirmContext.Provider value={ask}>
      {children}
      {request && <ConfirmDialog key={request.message} request={request} onAnswer={answer} />}
    </ConfirmContext.Provider>
  );
}

/** A modal <dialog>: the page behind is inert, Tab stays inside, Escape and a press on
 * the backdrop refuse. The refusing button takes the focus before a dangerous action, so
 * a stray Enter deletes nothing. */
function ConfirmDialog({
  request,
  onAnswer,
}: {
  request: Request;
  onAnswer: (yes: boolean) => void;
}) {
  const { t } = useTranslation();
  const textId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const actionRef = useRef<HTMLButtonElement>(null);
  const danger = request.tone !== "neutral";

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    (danger ? cancelRef : actionRef).current?.focus();
    return () => {
      if (dialog.open) dialog.close();
      // The dialog leaves the page with the answer, before the browser could hand the
      // focus back to what had it before the question; so it is done here.
      if (before?.isConnected) before.focus();
    };
  }, [danger]);

  return (
    <dialog
      ref={dialogRef}
      className="confirm"
      aria-labelledby={textId}
      onCancel={() => onAnswer(false)}
      onClick={(event) => {
        // The body fills the dialog, so only a press on the backdrop lands on it.
        if (event.target === dialogRef.current) onAnswer(false);
      }}
    >
      <div className="confirm-body">
        <p id={textId} className="confirm-text">
          {request.message}
        </p>
        <div className="confirm-actions">
          <button ref={cancelRef} type="button" className="btn" onClick={() => onAnswer(false)}>
            {request.cancel ?? t("common.cancel")}
          </button>
          <button
            ref={actionRef}
            type="button"
            className={danger ? "btn btn-danger" : "btn btn-p"}
            onClick={() => onAnswer(true)}
          >
            {request.action}
          </button>
        </div>
      </div>
    </dialog>
  );
}

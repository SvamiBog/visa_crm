import { useTranslation } from "react-i18next";
import { errorCode } from "../api";
import { dismissUndo, pauseUndo, resumeUndo, undoPending, useUndoToast } from "../undo";

/** «Заметка удалена · Отменить» at the bottom of the screen, above the phone's bar; after a
 * refused deletion, why it failed. The live region is always in the page, so a screen
 * reader announces what appears in it. */
export function UndoToast() {
  const { t } = useTranslation();
  const toast = useUndoToast();
  return (
    <div
      className="undo-region"
      role="status"
      onMouseEnter={pauseUndo}
      onMouseLeave={resumeUndo}
      onFocus={pauseUndo}
      onBlur={resumeUndo}
    >
      {toast?.kind === "pending" && (
        <div className="undo-toast">
          <span>{toast.message}</span>
          <button type="button" className="undo-button" onClick={undoPending}>
            {t("undo.undo")}
          </button>
        </div>
      )}
      {toast?.kind === "failed" && (
        <div className="undo-toast undo-failed">
          <span>
            {t("undo.failed")}{" "}
            {t(`errors.${errorCode(toast.error)}`, { defaultValue: t("errors.unknown") })}
          </span>
          <button type="button" className="undo-button" onClick={dismissUndo}>
            {t("undo.close")}
          </button>
        </div>
      )}
    </div>
  );
}

import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "./Icon";

// The password of the account (sign-in, password change, confirmations in «Настройки»)
// with an eye button inside the field that shows what was typed (UI audit 17.11.2026).
// Vault passwords do not use it: showing one of them is written to the log (pages/vault).
//
// The button keeps one name, «Показать пароль», and says whether the password is shown
// with aria-pressed (a toggle's name must not change with its state); its tooltip names
// what a click does. Sending the form hides the password again. `autoComplete` stays on
// the input, so password managers fill it and offer to save it.

type Props = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: "current-password" | "new-password";
  autoFocus?: boolean;
  minLength?: number;
  /** A rule under the field («Не короче 10 символов»), read out with the field. */
  hint?: string;
};

export function PasswordField({
  label,
  value,
  onChange,
  autoComplete,
  autoFocus,
  minLength,
  hint,
}: Props) {
  const { t } = useTranslation();
  const id = useId();
  const hintId = `${id}-hint`;
  const input = useRef<HTMLInputElement>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const form = input.current?.form;
    if (!form) return;
    const hide = () => setShown(false);
    form.addEventListener("submit", hide);
    return () => form.removeEventListener("submit", hide);
  }, []);

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="password-input">
        <input
          ref={input}
          id={id}
          type={shown ? "text" : "password"}
          autoComplete={autoComplete}
          // Shown as text, a password is still not a word: a phone must not capitalise
          // or correct it, and the spell checker must not send it anywhere.
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          required
          autoFocus={autoFocus}
          minLength={minLength}
          aria-describedby={hint ? hintId : undefined}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          className="password-toggle"
          aria-label={t("auth.showPassword")}
          aria-pressed={shown}
          aria-controls={id}
          title={t(shown ? "auth.hidePassword" : "auth.showPassword")}
          // A tap leaves the focus (and a phone's keyboard) in the field; the keyboard
          // still reaches the button with Tab.
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => setShown((on) => !on)}
        >
          <Icon name={shown ? "eyeOff" : "eye"} />
        </button>
      </div>
      {hint && (
        <small id={hintId} className="muted">
          {hint}
        </small>
      )}
    </div>
  );
}

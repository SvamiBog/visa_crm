import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Field } from "./Field";
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
  /** The field's own error, under it (components/Field.tsx). */
  error?: ReactNode;
};

export function PasswordField({
  label,
  value,
  onChange,
  autoComplete,
  autoFocus,
  minLength,
  hint,
  error,
}: Props) {
  const { t } = useTranslation();
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
    <Field label={label} hint={hint} error={error}>
      {(control) => (
        <div className="password-input">
          <input
            {...control}
            ref={input}
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
            value={value}
            onChange={(e) => onChange(e.target.value)}
          />
          <button
            type="button"
            className="password-toggle"
            aria-label={t("auth.showPassword")}
            aria-pressed={shown}
            aria-controls={control.id}
            title={t(shown ? "auth.hidePassword" : "auth.showPassword")}
            // A tap leaves the focus (and a phone's keyboard) in the field; the keyboard
            // still reaches the button with Tab.
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => setShown((on) => !on)}
          >
            <Icon name={shown ? "eyeOff" : "eye"} />
          </button>
        </div>
      )}
    </Field>
  );
}

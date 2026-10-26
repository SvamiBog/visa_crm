import { useId, useState } from "react";

// Show and hide a rarely used part of a screen with a real disclosure: the button tells
// whether its part is open (aria-expanded) and which part it opens (aria-controls). The
// part stays in the page, hidden, so the reference holds and the focus stays on the
// button. Spread `button` on a <button> and `panel` on the part it opens.

export type Disclosure = {
  open: boolean;
  button: {
    type: "button";
    "aria-expanded": boolean;
    "aria-controls": string;
    onClick: () => void;
  };
  panel: { id: string; hidden: boolean };
};

export function useDisclosure(initiallyOpen = false): Disclosure {
  const id = useId();
  const [open, setOpen] = useState(initiallyOpen);
  return {
    open,
    button: {
      type: "button",
      "aria-expanded": open,
      "aria-controls": id,
      onClick: () => setOpen((value) => !value),
    },
    panel: { id, hidden: !open },
  };
}

import { useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

interface Props {
  open: boolean;
  onClose(): void;
  title: string;
  children: ReactNode;
}

/**
 * Left-hand slide-over built on the native modal <dialog>: focus trapping, Escape to close
 * and inert background come from the browser instead of hand-rolled JS.
 */
export function Drawer({ open, onClose, title, children }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    } else if (!open && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      // A click whose target is the <dialog> itself landed on the backdrop.
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      className="fixed inset-y-0 left-0 m-0 h-dvh max-h-none w-[min(22rem,88vw)] max-w-none bg-transparent p-0 backdrop:bg-black/50"
    >
      <div className="flex h-full flex-col overflow-hidden border-r border-ink-200 bg-canvas shadow-xl">
        <div className="flex items-center justify-between border-b border-ink-200 bg-surface px-4 py-3">
          <h2 id={titleId} className="text-base font-semibold text-ink-900">
            {title}
          </h2>
          <button type="button" onClick={onClose} className="icon-btn" aria-label="Close">
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
      </div>
    </dialog>
  );
}

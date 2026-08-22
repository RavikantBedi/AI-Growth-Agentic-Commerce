/** Modal and Drawer. Both trap focus, close on ESC, and restore focus on exit. */
import { X } from "lucide-react";
import { useCallback, useEffect, useRef, type ReactNode } from "react";

import { IconButton } from "@/ui/primitives";
import { cn } from "@/lib/utils";

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

/**
 * Shared dialog behaviour: lock scroll, trap Tab inside, ESC to close, and
 * return focus to whatever opened it. Without the trap, keyboard users tab
 * straight out of a payment confirmation into the page behind it.
 */
function useDialog(
  open: boolean,
  onClose: () => void,
  ref: React.RefObject<HTMLElement | null>,
) {
  const restoreTo = useRef<HTMLElement | null>(null);

  const onKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !ref.current) return;
      const items = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null,
      );
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    },
    [onClose, ref],
  );

  useEffect(() => {
    if (!open) return;
    restoreTo.current = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown, true);

    // Focus the first control so keyboard users start inside the dialog.
    const timer = window.setTimeout(() => {
      const el = ref.current?.querySelector<HTMLElement>(FOCUSABLE);
      el?.focus();
    }, 30);

    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKeyDown, true);
      document.body.style.overflow = previousOverflow;
      restoreTo.current?.focus?.();
    };
  }, [open, onKeyDown, ref]);
}

/* -------------------------------------------------------------------------- */
/* Modal — centred on desktop, full-height sheet on mobile                     */
/* -------------------------------------------------------------------------- */
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
  dismissible = true,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  dismissible?: boolean;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  useDialog(open, dismissible ? onClose : () => {}, panelRef);

  if (!open) return null;

  const widths = {
    sm: "sm:max-w-md",
    md: "sm:max-w-lg",
    lg: "sm:max-w-2xl",
    xl: "sm:max-w-4xl",
  }[size];

  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center sm:items-center sm:p-4">
      <div
        className="absolute inset-0 bg-fg/40 backdrop-blur-[2px]"
        onClick={dismissible ? onClose : undefined}
        aria-hidden
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        className={cn(
          "relative flex max-h-[92dvh] w-full flex-col overflow-hidden bg-surface shadow-lg",
          "rounded-t-xl sm:rounded-xl border border-line animate-scale-in",
          widths,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-4 py-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-fg">{title}</div>
            {description && <p className="mt-0.5 text-xs text-fg-muted">{description}</p>}
          </div>
          {dismissible && <IconButton icon={X} label="Close" onClick={onClose} size="sm" />}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>

        {footer && <div className="border-t border-line bg-surface-muted/50 px-4 py-3">{footer}</div>}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Drawer — side panel on desktop, full-screen sheet on mobile                 */
/* -------------------------------------------------------------------------- */
export function Drawer({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: "md" | "lg";
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  useDialog(open, onClose, panelRef);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60]">
      <div className="absolute inset-0 bg-fg/40 backdrop-blur-[2px]" onClick={onClose} aria-hidden />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        className={cn(
          "absolute inset-y-0 right-0 flex w-full flex-col border-l border-line bg-surface shadow-lg",
          "animate-slide-in-right",
          width === "lg" ? "sm:max-w-3xl" : "sm:max-w-xl",
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-4 py-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-fg">{title}</div>
            {description && <p className="mt-0.5 text-xs text-fg-muted">{description}</p>}
          </div>
          <IconButton icon={X} label="Close panel" onClick={onClose} size="sm" />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>

        {footer && <div className="border-t border-line bg-surface-muted/50 px-4 py-3">{footer}</div>}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Confirm dialog                                                              */
/* -------------------------------------------------------------------------- */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmLabel = "Confirm",
  destructive,
  loading,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  destructive?: boolean;
  loading?: boolean;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      footer={
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="inline-flex h-9 items-center rounded-md border border-line bg-surface px-3.5 text-sm font-medium text-fg transition-colors hover:bg-surface-muted"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            className={cn(
              "inline-flex h-9 items-center rounded-md px-3.5 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50",
              destructive ? "bg-danger" : "bg-accent text-accent-fg",
            )}
          >
            {confirmLabel}
          </button>
        </div>
      }
    >
      <p className="text-sm leading-relaxed text-fg-muted">{message}</p>
    </Modal>
  );
}

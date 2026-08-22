/** Empty / error / loading states, callouts, toasts and status badges. */
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  FlaskConical,
  Info,
  Loader2,
  RefreshCw,
  ShieldCheck,
  ShieldX,
  X,
  XCircle,
} from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { Badge, Button, type Tone } from "@/ui/primitives";
import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/* Status badges — one vocabulary for every state in the product               */
/* -------------------------------------------------------------------------- */
type StatusSpec = { label: string; tone: Tone; icon?: React.ComponentType<{ className?: string }> };

/**
 * Status never relies on colour alone: each carries a label and most carry an
 * icon, so the meaning survives greyscale and screen readers.
 */
const STATUS: Record<string, StatusSpec> = {
  // Orders
  PAID: { label: "Paid", tone: "success", icon: CheckCircle2 },
  PAYMENT_FAILED: { label: "Payment failed", tone: "danger", icon: XCircle },
  PAYMENT_PENDING: { label: "Payment pending", tone: "warning", icon: Clock },
  CHECKOUT_PENDING: { label: "Awaiting approval", tone: "info", icon: Clock },
  CART: { label: "Cart", tone: "neutral" },
  DRAFT: { label: "Draft", tone: "neutral" },
  CANCELLED: { label: "Cancelled", tone: "neutral" },
  REFUNDED: { label: "Refunded", tone: "info" },

  // Payments
  CREATED: { label: "Created", tone: "neutral" },
  PENDING: { label: "Pending", tone: "warning", icon: Clock },
  AUTHORIZED: { label: "Authorized", tone: "warning" },
  CAPTURED: { label: "Captured", tone: "success", icon: CheckCircle2 },
  FAILED: { label: "Failed", tone: "danger", icon: XCircle },
  VERIFICATION_FAILED: { label: "Verification failed", tone: "danger", icon: ShieldX },
  UNKNOWN: { label: "Unknown", tone: "warning", icon: AlertTriangle },

  // Verification
  VERIFIED: { label: "Verified", tone: "success", icon: ShieldCheck },
  NOT_ATTEMPTED: { label: "Not attempted", tone: "neutral" },
  UNVERIFIABLE: { label: "Unverifiable", tone: "warning", icon: AlertTriangle },

  // Audit decisions
  ALLOWED: { label: "Allowed", tone: "success" },
  REJECTED: { label: "Rejected", tone: "danger" },
  INFO: { label: "Info", tone: "neutral" },

  // Campaigns
  ACTIVE: { label: "Active", tone: "success" },
  PENDING_APPROVAL: { label: "Needs approval", tone: "warning", icon: Clock },
  PAUSED: { label: "Paused", tone: "neutral" },
  ENDED: { label: "Ended", tone: "neutral" },
};

export function StatusBadge({
  status,
  className,
}: {
  status: string | null | undefined;
  className?: string;
}) {
  if (!status) return <span className="text-xs text-fg-faint">—</span>;
  const spec = STATUS[status] ?? { label: status.replace(/_/g, " ").toLowerCase(), tone: "neutral" as Tone };
  return (
    <Badge tone={spec.tone} icon={spec.icon} className={className}>
      {spec.label}
    </Badge>
  );
}

/** Marks any figure produced by the growth simulator rather than real activity. */
export function SyntheticBadge({ className }: { className?: string }) {
  return (
    <Badge tone="warning" icon={FlaskConical} className={className}>
      Synthetic
    </Badge>
  );
}

/* -------------------------------------------------------------------------- */
/* Callout                                                                     */
/* -------------------------------------------------------------------------- */
const CALLOUT: Record<Exclude<Tone, "brand" | "neutral">, {
  cls: string;
  Icon: React.ComponentType<{ className?: string }>;
}> = {
  info: { cls: "border-info/25 bg-info-soft", Icon: Info },
  success: { cls: "border-success/25 bg-success-soft", Icon: CheckCircle2 },
  warning: { cls: "border-warning/25 bg-warning-soft", Icon: AlertTriangle },
  danger: { cls: "border-danger/25 bg-danger-soft", Icon: XCircle },
};

const CALLOUT_FG: Record<string, string> = {
  info: "text-info",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
};

export function Callout({
  tone = "info",
  title,
  children,
  action,
  className,
}: {
  tone?: keyof typeof CALLOUT;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const { cls, Icon } = CALLOUT[tone];
  return (
    <div className={cn("flex gap-2.5 rounded-md border p-3", cls, className)}>
      <Icon className={cn("mt-px h-4 w-4 shrink-0", CALLOUT_FG[tone])} aria-hidden />
      <div className="min-w-0 flex-1">
        {title && <p className={cn("text-xs font-semibold", CALLOUT_FG[tone])}>{title}</p>}
        {children && (
          <div className={cn("text-xs leading-relaxed text-fg-muted", title && "mt-0.5")}>
            {children}
          </div>
        )}
      </div>
      {action && <div className="shrink-0 self-center">{action}</div>}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Empty / error / loading                                                     */
/* -------------------------------------------------------------------------- */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: React.ComponentType<{ className?: string }>;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center px-6 py-12 text-center", className)}>
      {Icon && (
        <div className="mb-3 rounded-lg border border-line bg-surface-muted p-2.5">
          <Icon className="h-5 w-5 text-fg-faint" aria-hidden />
        </div>
      )}
      <p className="text-sm font-medium text-fg">{title}</p>
      {description && (
        <p className="mt-1 max-w-sm text-xs leading-relaxed text-fg-muted">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/**
 * Error state. Always says what failed, why if known, and what to do next —
 * never a bare "something went wrong", and never a stack trace.
 */
export function ErrorState({
  title,
  message,
  onRetry,
  className,
}: {
  title: string;
  message?: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn("flex flex-col items-center justify-center px-6 py-12 text-center", className)}
    >
      <div className="mb-3 rounded-lg border border-danger/25 bg-danger-soft p-2.5">
        <AlertTriangle className="h-5 w-5 text-danger" aria-hidden />
      </div>
      <p className="text-sm font-medium text-fg">{title}</p>
      {message && (
        <p className="mt-1 max-w-sm text-xs leading-relaxed text-fg-muted">{message}</p>
      )}
      {onRetry && (
        <Button variant="secondary" size="sm" icon={RefreshCw} onClick={onRetry} className="mt-4">
          Try again
        </Button>
      )}
    </div>
  );
}

export function Spinner({ className, label }: { className?: string; label?: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <Loader2 className={cn("h-4 w-4 animate-spin", className)} aria-hidden />
      {label && <span className="text-xs text-fg-muted">{label}</span>}
      <span className="sr-only">Loading</span>
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* Toasts                                                                      */
/* -------------------------------------------------------------------------- */
type ToastTone = keyof typeof CALLOUT;
type Toast = { id: number; tone: ToastTone; title: string; body?: string };

const ToastContext = createContext<{
  push: (tone: ToastTone, title: string, body?: string) => void;
}>({ push: () => {} });

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((tone: ToastTone, title: string, body?: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, tone, title, body }]);
    // Problems linger; confirmations get out of the way.
    const ttl = tone === "danger" || tone === "warning" ? 8000 : 4000;
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), ttl);
  }, []);

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="pointer-events-none fixed bottom-4 right-4 z-[70] flex w-[min(23rem,calc(100vw-2rem))] flex-col gap-2"
      >
        {toasts.map((t) => {
          const { cls, Icon } = CALLOUT[t.tone];
          return (
            <div
              key={t.id}
              className={cn(
                "pointer-events-auto flex gap-2.5 rounded-md border p-3 shadow-md",
                "animate-fade-up bg-surface",
                cls,
              )}
            >
              <Icon className={cn("mt-px h-4 w-4 shrink-0", CALLOUT_FG[t.tone])} aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold text-fg">{t.title}</p>
                {t.body && <p className="mt-0.5 text-2xs leading-relaxed text-fg-muted">{t.body}</p>}
              </div>
              <button
                onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}
                className="shrink-0 self-start rounded p-0.5 text-fg-faint transition-colors hover:text-fg"
                aria-label="Dismiss notification"
              >
                <X className="h-3 w-3" aria-hidden />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

/* -------------------------------------------------------------------------- */
/* Environment indicator                                                       */
/* -------------------------------------------------------------------------- */
/**
 * Test-mode indicator. Always visible, never alarming — a merchant must never
 * mistake a simulated payment for a real one.
 */
export function EnvironmentBadge({
  provider,
  simulated,
  className,
}: {
  provider?: string;
  simulated?: boolean;
  className?: string;
}) {
  const full = simulated
    ? "Local sandbox"
    : provider === "razorpay_test"
      ? "Razorpay test mode"
      : "Test mode";
  const short = simulated ? "Sandbox" : "Test mode";
  return (
    <Badge
      tone={simulated ? "warning" : "info"}
      dot
      className={cn("font-semibold uppercase tracking-wide", className)}
    >
      <span className="hidden sm:inline">{full}</span>
      <span className="sm:hidden">{short}</span>
    </Badge>
  );
}

export { CALLOUT_FG };

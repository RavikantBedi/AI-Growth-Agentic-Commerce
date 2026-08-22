import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  CreditCard,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { PolicyChecks } from "@/components/merchant/AuditPage";
import { Callout, EnvironmentBadge, useToast } from "@/ui/feedback";
import { Modal } from "@/ui/overlays";
import { Badge, Button, KeyValue } from "@/ui/primitives";
import {
  ApiError,
  api,
  type PaymentResult,
  type Quote,
  type SandboxOutcome,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type Phase = "review" | "creating" | "paying" | "verifying" | "done";

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => { open: () => void };
  }
}

/**
 * The money gate, made visible.
 *
 * The dialog walks the exact sequence the backend enforces and never claims an
 * outcome the server has not confirmed: the success screen appears only after
 * `/api/payments/verify` returns `paid: true`.
 */
export function CheckoutDialog({
  open,
  quote,
  onClose,
  onPaid,
  onInspectOrder,
}: {
  open: boolean;
  quote: Quote | null;
  onClose: () => void;
  onPaid: (result: PaymentResult) => void;
  onInspectOrder?: (orderId: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>("review");
  const [result, setResult] = useState<PaymentResult | null>(null);
  const [failure, setFailure] = useState<{
    kind: "declined" | "unavailable" | "stale" | "policy";
    message: string;
  } | null>(null);
  const [sandboxOutcome, setSandboxOutcome] = useState<SandboxOutcome>("success");
  const toast = useToast();

  // One idempotency key per opened dialog: a double-click, a refresh or a
  // retried request all reuse it, so the backend replays instead of charging
  // twice.
  const idempotencyKey = useRef<string>("");

  useEffect(() => {
    if (open && quote) {
      setPhase("review");
      setResult(null);
      setFailure(null);
      idempotencyKey.current = `${quote.quote_id}:${crypto.randomUUID?.() ?? Date.now()}`;
    }
  }, [open, quote]);

  if (!quote) return null;

  const provider = quote.payment_provider;
  const isSandbox = provider.provider === "local_sandbox";
  const busy = phase === "creating" || phase === "paying" || phase === "verifying";

  const fail = (kind: NonNullable<typeof failure>["kind"], message: string) => {
    setFailure({ kind, message });
    setPhase("review");
  };

  async function handleConfirm() {
    if (!quote) return;
    setFailure(null);
    setPhase("creating");

    let payment;
    try {
      payment = await api.payments.confirm(quote.quote_id, "buyer", idempotencyKey.current);
    } catch (e) {
      const err = e as ApiError;
      fail(
        err.code === "provider_unavailable"
          ? "unavailable"
          : err.code === "quote_stale"
            ? "stale"
            : err.code === "policy_violation"
              ? "policy"
              : "declined",
        err.message,
      );
      return;
    }

    setPhase("paying");
    if (isSandbox) {
      await paySandbox(payment.provider_order_id);
    } else {
      await payRazorpay(payment.provider_order_id, payment.amount_paise, payment.provider_config);
    }
  }

  async function paySandbox(orderId: string) {
    try {
      const attempt = await api.payments.sandboxPay(orderId, sandboxOutcome);
      await verify(attempt.provider_order_id, attempt.provider_payment_id, attempt.signature);
    } catch (e) {
      fail("unavailable", (e as ApiError).message);
    }
  }

  async function payRazorpay(
    orderId: string,
    amountPaise: number,
    config: Quote["payment_provider"],
  ) {
    const loaded = await loadRazorpayScript(config.checkout_script);
    if (!loaded || !window.Razorpay) {
      fail(
        "unavailable",
        "Could not load the Razorpay checkout script. Check your connection and try again — nothing has been charged.",
      );
      return;
    }

    const rzp = new window.Razorpay({
      key: config.key_id,
      order_id: orderId,
      amount: amountPaise,
      currency: quote?.cart.currency ?? "INR",
      name: "Nova Electronics",
      description: `Test-mode order ${quote?.order_id ?? ""}`,
      notes: { order_id: quote?.order_id ?? "" },
      theme: { color: "#4f46e5" },
      // The handler payload is a *claim*. It is sent to the backend to be
      // verified; it is never treated as proof of payment here.
      handler: (response: Record<string, string>) => {
        void verify(
          response.razorpay_order_id,
          response.razorpay_payment_id,
          response.razorpay_signature,
        );
      },
      modal: {
        ondismiss: () => {
          void api.payments
            .reportFailure(orderId, "Checkout dismissed by the shopper.")
            .then((r) => {
              setResult(r);
              setPhase("done");
            })
            .catch(() =>
              fail("declined", "Checkout was closed before payment completed. Nothing has been charged."),
            );
        },
      },
    });
    rzp.open();
  }

  async function verify(orderId: string, paymentId: string, signature: string) {
    setPhase("verifying");
    try {
      const verified = await api.payments.verify(orderId, paymentId, signature);
      setResult(verified);
      setPhase("done");
      if (verified.paid) {
        onPaid(verified);
        toast.push("success", "Payment verified", verified.user_message);
      } else {
        toast.push("danger", "Payment not completed", verified.user_message);
      }
    } catch (e) {
      fail("unavailable", (e as ApiError).message);
      toast.push("warning", "Could not verify payment", (e as ApiError).message);
    }
  }

  async function handleReconcile() {
    setPhase("verifying");
    try {
      const reconciled = await api.payments.reconcile(quote!.order_id);
      setResult(reconciled);
      setPhase("done");
      if (reconciled.paid) onPaid(reconciled);
      toast.push(
        reconciled.paid ? "success" : "info",
        reconciled.paid ? "Reconciled — payment confirmed" : "Reconciliation complete",
        reconciled.message,
      );
    } catch (e) {
      setPhase("done");
      toast.push("warning", "Still could not reach the provider", (e as ApiError).message);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      dismissible={!busy}
      size="lg"
      title={
        <span className="flex flex-wrap items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-brand" aria-hidden />
          {phase === "done" ? "Payment result" : "Review & pay"}
          <EnvironmentBadge provider={provider.provider} simulated={isSandbox} />
        </span>
      }
      description={
        phase === "done" ? undefined : "Nothing is charged until you approve this exact amount."
      }
      footer={
        phase === "done" ? (
          <DoneFooter
            result={result}
            onClose={onClose}
            onReconcile={() => void handleReconcile()}
            onInspectOrder={onInspectOrder}
          />
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-2xs text-fg-faint">
              By confirming you approve this exact purchase for{" "}
              <span className="tnum font-medium text-fg">{quote.cart.total_display}</span>.
            </p>
            <div className="flex gap-2">
              <Button onClick={onClose} disabled={busy}>
                Cancel
              </Button>
              <Button
                variant="primary"
                icon={CreditCard}
                loading={busy}
                onClick={() => void handleConfirm()}
              >
                {phase === "creating"
                  ? "Creating payment order…"
                  : phase === "paying"
                    ? "Awaiting payment…"
                    : phase === "verifying"
                      ? "Verifying with provider…"
                      : `Confirm & pay ${quote.cart.total_display}`}
              </Button>
            </div>
          </div>
        )
      }
    >
      {phase === "done" && result ? (
        <PaymentOutcome result={result} />
      ) : (
        <div className="space-y-4">
          <StepIndicator phase={phase} />

          {failure && <FailureNotice kind={failure.kind} message={failure.message} />}

          <section>
            <h3 className="mb-2 text-xs font-semibold text-fg">You're about to purchase</h3>
            <div className="overflow-hidden rounded-md border border-line">
              <table className="w-full text-xs">
                <caption className="sr-only">Order summary</caption>
                <thead className="bg-surface-muted/60 text-fg-muted">
                  <tr>
                    <th scope="col" className="px-3 py-2 text-left font-medium">Item</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Qty</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Unit</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Amount</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {quote.cart.lines.map((line) => (
                    <tr key={line.product_id}>
                      <td className="px-3 py-2">
                        <span className="text-fg">{line.name}</span>
                        {line.source !== "direct" && (
                          <Badge tone="brand" className="ml-1.5">Recommended add-on</Badge>
                        )}
                      </td>
                      <td className="tnum px-3 py-2 text-right text-fg-muted">{line.quantity}</td>
                      <td className="tnum px-3 py-2 text-right text-fg-muted">
                        {line.unit_price_display}
                      </td>
                      <td className="tnum px-3 py-2 text-right text-fg">
                        {line.line_total_display}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot className="border-t border-line bg-surface-muted/40">
                  <tr>
                    <td colSpan={3} className="px-3 py-1.5 text-right text-fg-muted">Subtotal</td>
                    <td className="tnum px-3 py-1.5 text-right text-fg-muted">
                      {quote.cart.subtotal_display}
                    </td>
                  </tr>
                  {quote.cart.discount_paise > 0 && (
                    <tr>
                      <td colSpan={3} className="px-3 py-1.5 text-right text-success">
                        Discount — {quote.cart.discount_label}
                      </td>
                      <td className="tnum px-3 py-1.5 text-right text-success">
                        −{quote.cart.discount_display}
                      </td>
                    </tr>
                  )}
                  {quote.cart.tax_paise > 0 && (
                    <tr>
                      <td colSpan={3} className="px-3 py-1.5 text-right text-fg-muted">
                        GST @ {quote.cart.tax_percent}%
                      </td>
                      <td className="tnum px-3 py-1.5 text-right text-fg-muted">
                        {quote.cart.tax_display}
                      </td>
                    </tr>
                  )}
                  <tr className="border-t border-line">
                    <td colSpan={3} className="px-3 py-2.5 text-right text-sm font-semibold text-fg">
                      Total ({quote.cart.currency})
                    </td>
                    <td className="tnum px-3 py-2.5 text-right text-sm font-semibold text-fg">
                      {quote.cart.total_display}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </section>

          <section>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-fg">
              <ShieldCheck className="h-3.5 w-3.5 text-success" aria-hidden />
              Policy checks — {quote.policy_result.checks.filter((c) => c.passed).length} of{" "}
              {quote.policy_result.checks.length} passed
            </h3>
            <PolicyChecks checks={quote.policy_result.checks} />
          </section>

          {isSandbox ? <SandboxControls value={sandboxOutcome} onChange={setSandboxOutcome} /> : <RazorpayTestHelp />}

          <Callout tone="info">{quote.notice}</Callout>
        </div>
      )}
    </Modal>
  );
}

/* -------------------------------------------------------------------------- */
/* Progress                                                                    */
/* -------------------------------------------------------------------------- */
function StepIndicator({ phase }: { phase: Phase }) {
  const steps = [
    { id: "review", label: "Review" },
    { id: "creating", label: "Create order" },
    { id: "paying", label: "Pay" },
    { id: "verifying", label: "Server verifies" },
  ];
  const active = steps.findIndex((s) => s.id === phase);
  return (
    <ol className="flex gap-1.5" aria-label="Checkout progress">
      {steps.map((step, i) => (
        <li key={step.id} className="flex-1">
          <div
            className={cn(
              "h-1 rounded-full transition-colors",
              i <= active ? "bg-brand" : "bg-surface-muted",
            )}
          />
          <p
            className={cn(
              "mt-1 text-2xs",
              i === active ? "font-medium text-brand" : "text-fg-faint",
            )}
          >
            {step.label}
          </p>
        </li>
      ))}
    </ol>
  );
}

/* -------------------------------------------------------------------------- */
/* Failure notices — each state gets its own explanation and next action       */
/* -------------------------------------------------------------------------- */
function FailureNotice({
  kind,
  message,
}: {
  kind: "declined" | "unavailable" | "stale" | "policy";
  message: string;
}) {
  const spec = {
    declined: {
      tone: "danger" as const,
      title: "Payment not completed",
      extra: "Your order has NOT been marked as paid and nothing was charged.",
    },
    unavailable: {
      tone: "warning" as const,
      title: "Payment provider unavailable",
      extra:
        "We could not reach the payment provider, so no payment was created. Your order has not been marked as paid.",
    },
    stale: {
      tone: "warning" as const,
      title: "The cart changed after you approved it",
      extra: "Review the new total and approve it before paying — you will never be charged an amount you did not see.",
    },
    policy: {
      tone: "danger" as const,
      title: "Blocked by purchase policy",
      extra: "This order cannot proceed until the issue below is resolved.",
    },
  }[kind];

  return (
    <Callout tone={spec.tone} title={spec.title}>
      {message} {spec.extra}
    </Callout>
  );
}

/* -------------------------------------------------------------------------- */
/* Provider helpers                                                            */
/* -------------------------------------------------------------------------- */
function SandboxControls({
  value,
  onChange,
}: {
  value: SandboxOutcome;
  onChange: (v: SandboxOutcome) => void;
}) {
  const options: [SandboxOutcome, string][] = [
    ["success", "Succeeds"],
    ["failure", "Declined by bank"],
    ["tampered_signature", "Forged signature"],
    ["authorized_only", "Authorized, needs capture"],
    ["provider_outage", "Provider outage"],
  ];
  return (
    <section className="rounded-md border border-warning/25 bg-warning-soft p-3">
      <h3 className="text-xs font-semibold text-warning">Local sandbox</h3>
      <p className="mt-1 text-2xs leading-relaxed text-fg-muted">
        No Razorpay credentials are configured, so payments are simulated locally. The signature
        is a real HMAC and is verified server-side through the same code path Razorpay uses — a
        tampered signature genuinely fails. No real money is charged.
      </p>
      <fieldset className="mt-2.5">
        <legend className="sr-only">Choose the simulated payment outcome</legend>
        <div className="flex flex-wrap gap-1.5">
          {options.map(([v, label]) => (
            <button
              key={v}
              onClick={() => onChange(v)}
              aria-pressed={value === v}
              className={cn(
                "rounded border px-2 py-1 text-2xs transition-colors",
                value === v
                  ? "border-warning bg-warning text-white"
                  : "border-line bg-surface text-fg-muted hover:text-fg",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </fieldset>
    </section>
  );
}

/**
 * Razorpay test-mode credentials, shown where they are needed.
 *
 * Two things trip people up and both are addressed inline: the widely-quoted
 * 4111… card is on Razorpay's *international* list (Indian accounts reject it),
 * and test mode sends no OTP SMS — any 4–10 digit number succeeds.
 */
function RazorpayTestHelp() {
  const [copied, setCopied] = useState<string | null>(null);

  const copy = async (label: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      /* clipboard blocked — the number is on screen to type manually */
    }
  };

  const cards: [string, string, string][] = [
    ["Visa", "4100 2800 0000 1007", "4100280000001007"],
    ["Mastercard", "5500 6700 0000 1002", "5500670000001002"],
    ["RuPay", "6527 6589 0000 1005", "6527658900001005"],
  ];

  return (
    <section className="rounded-md border border-info/25 bg-info-soft p-3">
      <h3 className="flex items-center gap-1.5 text-xs font-semibold text-info">
        <CreditCard className="h-3.5 w-3.5" aria-hidden />
        Razorpay test mode — use these details
      </h3>

      <ul className="mt-2 space-y-1">
        {cards.map(([network, shown, raw]) => (
          <li key={network} className="flex items-center justify-between gap-3">
            <span className="text-2xs text-fg-muted">{network}</span>
            <span className="flex items-center gap-1.5">
              <span className="tnum font-mono text-2xs text-fg">{shown}</span>
              <button
                onClick={() => void copy(network, raw)}
                className="rounded p-0.5 text-fg-faint transition-colors hover:text-fg"
                aria-label={`Copy ${network} test card number`}
              >
                {copied === network ? (
                  <CheckCircle2 className="h-3 w-3 text-success" aria-hidden />
                ) : (
                  <Copy className="h-3 w-3" aria-hidden />
                )}
              </button>
            </span>
          </li>
        ))}
        <li className="flex items-center justify-between gap-3">
          <span className="text-2xs text-fg-muted">Expiry / CVV</span>
          <span className="text-2xs text-fg">Any future date · any 3 digits</span>
        </li>
      </ul>

      <div className="mt-2.5 space-y-2">
        <p className="rounded border border-info/25 bg-surface p-2 text-2xs leading-relaxed text-fg-muted">
          <strong className="text-fg">On the OTP screen, type any 4–10 digit number</strong> (for
          example 123456). Test mode sends no SMS, so there is no real code to wait for. Fewer
          than 4 digits fails the payment, which is a convenient way to demo the failure path.
        </p>
        <p className="rounded border border-danger/25 bg-surface p-2 text-2xs leading-relaxed text-fg-muted">
          <strong className="text-danger">"International cards are not supported"?</strong> That
          means the card 4111 1111 1111 1111 was used — it is Razorpay's <em>international</em>{" "}
          test card, and Indian accounts have international payments off by default. Use one of
          the numbers above instead.
        </p>
      </div>
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* Outcome states                                                              */
/* -------------------------------------------------------------------------- */
function PaymentOutcome({ result }: { result: PaymentResult }) {
  const verificationFailed = result.payment_status === "VERIFICATION_FAILED";
  const spec = result.paid
    ? {
        Icon: CheckCircle2,
        cls: "text-success",
        ring: "border-success/25 bg-success-soft",
        title: "Payment verified",
        blurb: "Your payment was verified with the payment provider before this order was marked paid.",
      }
    : verificationFailed
      ? {
          Icon: AlertTriangle,
          cls: "text-warning",
          ring: "border-warning/25 bg-warning-soft",
          title: "Payment could not be verified",
          blurb: "The payment could not be proven authentic, so the order was NOT marked as paid.",
        }
      : {
          Icon: XCircle,
          cls: "text-danger",
          ring: "border-danger/25 bg-danger-soft",
          title: "Payment not completed",
          blurb: "Your order has NOT been marked as paid. Nothing was charged.",
        };

  return (
    <div className="space-y-4">
      <div className={cn("flex flex-col items-center gap-3 rounded-md border p-5 text-center", spec.ring)}>
        <spec.Icon className={cn("h-8 w-8", spec.cls)} aria-hidden />
        <div>
          <p className={cn("text-sm font-semibold", spec.cls)}>{spec.title}</p>
          <p className="tnum mt-1 text-2xl font-semibold text-fg">{result.amount_display}</p>
          <p className="mt-0.5 font-mono text-2xs text-fg-faint">{result.order_id}</p>
        </div>
        <p className="max-w-sm text-xs leading-relaxed text-fg-muted">{spec.blurb}</p>
      </div>

      <Callout tone={result.paid ? "success" : "danger"} title="What this means">
        {result.user_message}
      </Callout>

      <dl className="rounded-md border border-line px-3 py-1">
        <KeyValue label="Order status" mono>{result.order_status}</KeyValue>
        <KeyValue label="Payment status" mono>{result.payment_status}</KeyValue>
        <KeyValue label="Verification" mono>{result.verification_status}</KeyValue>
        <KeyValue label="Provider" mono>{result.provider}</KeyValue>
        <KeyValue label="Provider payment id" mono>
          {result.provider_payment_id ?? "—"}
        </KeyValue>
        <KeyValue label="Test mode" mono>{String(result.test_mode)}</KeyValue>
      </dl>

      {!result.paid && (
        <Callout tone="info" title="What happens next">
          The provider's own record is the source of truth. If money did leave your account,
          "Re-check payment status" reconciles against the provider and will move this order to
          paid only if a matching captured payment actually exists.
        </Callout>
      )}
    </div>
  );
}

function DoneFooter({
  result,
  onClose,
  onReconcile,
  onInspectOrder,
}: {
  result: PaymentResult | null;
  onClose: () => void;
  onReconcile: () => void;
  onInspectOrder?: (orderId: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-2xs text-fg-faint">
        {result?.paid
          ? "Recorded as PAID after server-side verification."
          : "No charge was recorded against this order."}
      </p>
      <div className="flex flex-wrap gap-2">
        {!result?.paid && (
          <Button icon={RefreshCw} onClick={onReconcile}>
            Re-check payment status
          </Button>
        )}
        {result && onInspectOrder && (
          <Button onClick={() => onInspectOrder(result.order_id)}>View audit trail</Button>
        )}
        <Button variant="primary" onClick={onClose}>
          {result?.paid ? "Continue shopping" : "Return to cart"}
        </Button>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
let scriptPromise: Promise<boolean> | null = null;

function loadRazorpayScript(src?: string): Promise<boolean> {
  if (window.Razorpay) return Promise.resolve(true);
  if (!src) return Promise.resolve(false);
  if (scriptPromise) return scriptPromise;

  scriptPromise = new Promise<boolean>((resolve) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = () => resolve(true);
    script.onerror = () => {
      scriptPromise = null;
      resolve(false);
    };
    document.body.appendChild(script);
  });
  return scriptPromise;
}

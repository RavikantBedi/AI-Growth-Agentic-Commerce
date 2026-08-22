import {
  Bot,
  LayoutGrid,
  Minus,
  Plus,
  RotateCcw,
  Send,
  ShieldAlert,
  ShieldCheck,
  ShoppingBag,
  Sparkles,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { BrowsePanel } from "@/components/buyer/BrowsePanel";
import { CheckoutDialog } from "@/components/buyer/CheckoutDialog";
import { ProductCard, UpsellCard } from "@/components/buyer/ProductCard";
import { RichText } from "@/components/buyer/RichText";
import { Callout, EmptyState, Spinner, useToast } from "@/ui/feedback";
import { Badge, Button, IconButton, Skeleton } from "@/ui/primitives";
import {
  ApiError,
  api,
  type Cart,
  type ChatResponse,
  type Health,
  type PaymentResult,
  type Product,
  type Quote,
  type UpsellSuggestion,
} from "@/lib/api";
import { cn } from "@/lib/utils";

interface Turn {
  id: string;
  role: "user" | "agent";
  text: string;
  response?: ChatResponse;
}

/**
 * The backend appends a degradation footnote wrapped in `_(…)_`. Splitting it
 * out lets it render as a styled note instead of literal underscores — which
 * is what a raw markdown emphasis marker looks like in a plain-text bubble.
 */
function splitFootnote(text: string): { body: string; note?: string } {
  const match = text.match(/\s*_\((.+)\)_\s*$/s);
  if (!match || match.index === undefined) return { body: text };
  return { body: text.slice(0, match.index).trimEnd(), note: match[1] };
}

const SUGGESTED = [
  "I need a laptop for programming under ₹80,000",
  "A camera for travel photography under ₹70,000",
  "Show me a phone under ₹30,000",
  "What's your return policy?",
];

export function BuyerInterface({
  health,
  onInspectOrder,
}: {
  health: Health | null;
  onInspectOrder?: (orderId: string) => void;
}) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [booting, setBooting] = useState(true);
  const [cart, setCart] = useState<Cart | null>(null);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [checkoutOpen, setCheckoutOpen] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [upsells, setUpsells] = useState<UpsellSuggestion[]>([]);
  const [merchantName, setMerchantName] = useState("the store");
  const [degraded, setDegraded] = useState(false);
  const [degradedNote, setDegradedNote] = useState("");
  const [browseOpen, setBrowseOpen] = useState(false);
  const [cartOpen, setCartOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  /* ---------------------------------------------------------------- boot */
  useEffect(() => {
    let cancelled = false;
    // `?session=<id>` resumes an existing conversation rather than opening a
    // new one — useful for picking a demo back up.
    const resumeId = new URLSearchParams(window.location.search).get("session");

    async function boot() {
      try {
        const started = await api.buyer.startSession("web-buyer");
        if (cancelled) return;
        setMerchantName(started.merchant.name);
        setDegraded(started.ai.degraded);

        if (!resumeId) {
          setSessionId(started.session_id);
          setTurns([{ id: "greeting", role: "agent", text: started.greeting }]);
          return;
        }

        setSessionId(resumeId);
        const [history, existing] = await Promise.all([
          api.buyer.history(resumeId),
          api.buyer.cart(resumeId),
        ]);
        if (cancelled) return;
        setCart(existing);
        setTurns(
          history.messages.length
            ? history.messages.map((m, i) => ({ id: `h-${i}`, role: m.role, text: m.content }))
            : [{ id: "greeting", role: "agent", text: started.greeting }],
        );
      } catch (e) {
        if (!cancelled) toast.push("danger", "Could not start a session", (e as ApiError).message);
      } finally {
        if (!cancelled) setBooting(false);
      }
    }

    void boot();
    return () => {
      cancelled = true;
    };
  }, [toast]);

  // `?checkout=1` on a resumed session opens Review & pay straight away, so a
  // half-finished basket can be picked up (or demoed) from a link.
  const autoCheckout = useRef(false);
  useEffect(() => {
    if (autoCheckout.current || booting || !sessionId || !cart || cart.item_count === 0) return;
    if (new URLSearchParams(window.location.search).get("checkout") !== "1") return;
    autoCheckout.current = true;
    void startCheckout();
    // `startCheckout` is stable for this purpose — it only reads sessionId.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [booting, sessionId, cart]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns, sending]);

  const refreshCart = useCallback(async () => {
    if (!sessionId) return;
    try {
      setCart(await api.buyer.cart(sessionId));
    } catch {
      /* a cart read failure must not break the conversation */
    }
  }, [sessionId]);

  async function startNewConversation() {
    try {
      const started = await api.buyer.startSession("web-buyer");
      setSessionId(started.session_id);
      setTurns([{ id: "greeting", role: "agent", text: started.greeting }]);
      setCart(null);
      setUpsells([]);
      setQuote(null);
      setDegraded(started.ai.degraded);
      window.history.replaceState({}, "", window.location.pathname + window.location.hash);
      toast.push("info", "Started a new conversation");
      inputRef.current?.focus();
    } catch (e) {
      toast.push("danger", "Could not start a new conversation", (e as ApiError).message);
    }
  }

  /* ---------------------------------------------------------------- chat */
  async function send(message: string) {
    if (!sessionId || !message.trim() || sending) return;
    const text = message.trim();
    setInput("");
    setTurns((t) => [...t, { id: `u-${Date.now()}`, role: "user", text }]);
    setSending(true);

    try {
      const response = await api.buyer.chat(sessionId, text);
      setTurns((t) => [
        ...t,
        { id: `a-${Date.now()}`, role: "agent", text: response.message, response },
      ]);
      if (response.cart) setCart(response.cart);
      setUpsells(response.upsells);
      setDegraded(response.ai.degraded);
      setDegradedNote(response.ai.degraded_reason);

      if (response.security.shopper_message_injection.detected) {
        toast.push(
          "warning",
          "Instruction-injection pattern detected",
          "Your message was handled as a shopping request only. Policy, pricing and the payment confirmation are unaffected.",
        );
      }
      if (response.checkout) {
        setQuote(response.checkout);
        setCheckoutOpen(true);
      }
      if (response.checkout_error) {
        toast.push("warning", "Checkout not possible yet", response.message);
      }
    } catch (e) {
      const err = e as ApiError;
      setTurns((t) => [
        ...t,
        { id: `e-${Date.now()}`, role: "agent", text: `Sorry — ${err.message}` },
      ]);
      toast.push("danger", "Message failed", err.message);
    } finally {
      setSending(false);
    }
  }

  /* ---------------------------------------------------------------- cart */
  async function addProduct(product: Product, source = "direct") {
    if (!sessionId) return;
    try {
      setCart(await api.buyer.addToCart(sessionId, product.id, 1, source));
      toast.push("success", "Added to cart", `${product.name} — ${product.price_display}`);
      const follow = await api.buyer.chat(sessionId, "what else goes with this?");
      setUpsells(follow.upsells);
    } catch (e) {
      toast.push("warning", "Could not add that", (e as ApiError).message);
    }
  }

  async function acceptUpsell(s: UpsellSuggestion) {
    if (!sessionId) return;
    try {
      setCart(await api.buyer.addToCart(sessionId, s.product_id, 1, s.kind));
      setUpsells((u) => u.filter((x) => x.product_id !== s.product_id));
      toast.push(
        "success",
        `Added ${s.name}`,
        `+${s.incremental_display} — new total ${s.new_total_display}`,
      );
    } catch (e) {
      toast.push("warning", "Could not add that add-on", (e as ApiError).message);
    }
  }

  async function changeQuantity(productId: string, quantity: number) {
    if (!sessionId) return;
    try {
      setCart(await api.buyer.setQuantity(sessionId, productId, quantity));
    } catch (e) {
      toast.push("warning", "Could not update the cart", (e as ApiError).message);
      void refreshCart();
    }
  }

  async function removeLine(productId: string) {
    if (!sessionId) return;
    try {
      setCart(await api.buyer.removeFromCart(sessionId, productId));
    } catch (e) {
      toast.push("warning", "Could not remove that", (e as ApiError).message);
    }
  }

  /* ------------------------------------------------------------ checkout */
  async function startCheckout() {
    if (!sessionId || preparing) return;
    setPreparing(true);
    try {
      setQuote(await api.payments.prepare(sessionId));
      setCheckoutOpen(true);
    } catch (e) {
      const err = e as ApiError;
      const detail = err.detail as
        | { policy_result?: { violations?: { message: string }[] } }
        | null;
      const violations = detail?.policy_result?.violations;
      toast.push(
        "danger",
        "Checkout blocked by policy",
        violations?.length ? violations.map((v) => v.message).join(" ") : err.message,
      );
    } finally {
      setPreparing(false);
    }
  }

  function handlePaid(result: PaymentResult) {
    void refreshCart();
    setUpsells([]);
    setTurns((t) => [
      ...t,
      {
        id: `paid-${Date.now()}`,
        role: "agent",
        text: `${result.user_message} Your order id is ${result.order_id}.`,
      },
    ]);
  }

  const simulated = health?.payment_provider.simulated ?? false;

  return (
    <div className="flex h-full min-h-0 overflow-x-clip">
      {/* ------------------------------------------------- conversation */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-line bg-surface px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="relative grid h-8 w-8 shrink-0 place-items-center rounded-md bg-brand-soft">
              <Bot className="h-4 w-4 text-brand" aria-hidden />
              {!degraded && (
                <span
                  className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-success ring-2 ring-surface"
                  aria-hidden
                />
              )}
            </span>
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-fg">{merchantName}</h2>
              <p className="truncate text-2xs text-fg-muted">
                Ask for what you need — nothing is charged without your approval.
              </p>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1.5">
            <Button
              size="sm"
              icon={LayoutGrid}
              onClick={() => setBrowseOpen((b) => !b)}
              aria-pressed={browseOpen}
              className={cn(browseOpen && "border-brand text-brand")}
            >
              <span className="hidden sm:inline">Browse</span>
            </Button>
            <Button size="sm" icon={RotateCcw} onClick={() => void startNewConversation()}>
              <span className="hidden sm:inline">New chat</span>
            </Button>
            <button
              onClick={() => setCartOpen(true)}
              className="relative inline-flex h-8 items-center gap-1.5 rounded-md border border-line px-2.5 text-xs font-medium text-fg-muted transition-colors hover:text-fg lg:hidden"
              aria-label={`Open cart, ${cart?.item_count ?? 0} items`}
            >
              <ShoppingBag className="h-3.5 w-3.5" aria-hidden />
              {cart && cart.item_count > 0 && (
                <span className="tnum absolute -right-1 -top-1 rounded-full bg-brand px-1.5 text-[9px] font-semibold text-white">
                  {cart.item_count}
                </span>
              )}
            </button>
          </div>
        </div>

        {degraded && (
          <div className="flex shrink-0 items-start gap-2 border-b border-line bg-warning-soft px-4 py-2">
            <ShieldAlert className="mt-px h-3.5 w-3.5 shrink-0 text-warning" aria-hidden />
            <p className="min-w-0 flex-1 text-2xs leading-relaxed text-fg-muted">
              <span className="font-medium text-warning">
                {degradedNote.toLowerCase().includes("rate limit")
                  ? "Model rate-limited"
                  : "No language model connected"}
              </span>{" "}
              — replies come from the deterministic catalog engine. Search, recommendations,
              cart and checkout all work exactly the same.
            </p>
          </div>
        )}

        <BrowsePanel
          open={browseOpen}
          onClose={() => setBrowseOpen(false)}
          onAdd={(p) => void addProduct(p)}
        />

        <div
          ref={scrollRef}
          className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4"
          role="log"
          aria-live="polite"
          aria-label="Conversation"
        >
          {booting ? (
            <div className="space-y-3">
              <Skeleton className="h-16 w-3/4" />
              <Skeleton className="h-4 w-1/3" />
            </div>
          ) : (
            turns.map((turn) => (
              <TurnView key={turn.id} turn={turn} onAdd={(p) => void addProduct(p)} />
            ))
          )}

          {sending && (
            <div className="flex items-center gap-2 pl-9">
              <Spinner className="h-3 w-3" />
              <span className="text-xs text-fg-muted">Searching the catalog…</span>
            </div>
          )}

          {!booting && turns.length <= 1 && !sending && (
            <div className="pl-9">
              <p className="mb-2 text-2xs uppercase tracking-wide text-fg-faint">Try asking</p>
              <div className="flex flex-wrap gap-1.5">
                {SUGGESTED.map((prompt) => (
                  <button
                    key={prompt}
                    onClick={() => void send(prompt)}
                    className="rounded-full border border-line bg-surface px-3 py-1.5 text-2xs text-fg-muted transition-colors hover:border-brand hover:text-fg"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {upsells.length > 0 && (
          <div className="shrink-0 border-t border-line bg-surface px-4 py-3">
            <p className="mb-2 flex items-center gap-1.5 text-2xs font-medium text-brand">
              <Sparkles className="h-3 w-3" aria-hidden />
              Recommended with your cart — each shows its exact price impact
            </p>
            <div className="space-y-1.5">
              {upsells.map((s) => (
                <UpsellCard key={s.product_id} suggestion={s} onAccept={acceptUpsell} />
              ))}
            </div>
          </div>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
          className="shrink-0 border-t border-line bg-surface px-4 py-3"
        >
          <div className="flex gap-2">
            <label htmlFor="composer" className="sr-only">
              Message the shopping assistant
            </label>
            <input
              id="composer"
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={!sessionId || sending}
              placeholder="e.g. I need a laptop for programming under ₹80,000"
              className="h-10 flex-1 rounded-md border border-line bg-surface px-3 text-sm text-fg placeholder:text-fg-faint focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand disabled:opacity-60"
            />
            <Button
              type="submit"
              variant="primary"
              size="lg"
              disabled={!sessionId || sending || !input.trim()}
              aria-label="Send message"
              className="w-11 px-0"
            >
              <Send className="h-4 w-4" aria-hidden />
            </Button>
          </div>
        </form>
      </div>

      {/* -------------------------------------------------------- cart */}
      {cartOpen && (
        <div
          className="fixed inset-0 z-40 bg-fg/40 lg:hidden"
          onClick={() => setCartOpen(false)}
          aria-hidden
        />
      )}

      <aside
        aria-label="Cart"
        className={cn(
          "flex shrink-0 flex-col border-l border-line bg-surface",
          "fixed inset-y-0 right-0 z-50 w-[20rem] lg:static lg:z-auto",
          // Hidden, not translated: a closed drawer must leave the tab order.
          cartOpen ? "flex animate-slide-in-right" : "hidden lg:flex",
        )}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-line px-4 py-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
            <ShoppingBag className="h-4 w-4 text-fg-muted" aria-hidden />
            Your cart
          </h2>
          <div className="flex items-center gap-2">
            {cart && cart.item_count > 0 && <Badge tone="brand">{cart.item_count}</Badge>}
            <IconButton
              icon={X}
              label="Close cart"
              size="sm"
              onClick={() => setCartOpen(false)}
              className="lg:hidden"
            />
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {!cart || cart.item_count === 0 ? (
            <EmptyState
              icon={ShoppingBag}
              title="Nothing here yet"
              description="Tell the assistant what you're looking for and it will search the real catalog."
            />
          ) : (
            <ul className="divide-y divide-line">
              {cart.lines.map((line) => (
                <li key={line.product_id} className="p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-xs font-medium text-fg" title={line.name}>
                        {line.name}
                      </p>
                      <p className="tnum mt-0.5 text-2xs text-fg-faint">
                        {line.unit_price_display} each
                      </p>
                      {line.source !== "direct" && (
                        <Badge tone="brand" className="mt-1">
                          {line.source === "upsell" ? "upsell" : "cross-sell"}
                        </Badge>
                      )}
                    </div>
                    <IconButton
                      icon={Trash2}
                      label={`Remove ${line.name}`}
                      size="sm"
                      onClick={() => void removeLine(line.product_id)}
                    />
                  </div>
                  <div className="mt-2 flex items-center justify-between">
                    <div className="flex items-center gap-1">
                      <IconButton
                        icon={Minus}
                        label={`Decrease quantity of ${line.name}`}
                        size="sm"
                        variant="secondary"
                        onClick={() => void changeQuantity(line.product_id, line.quantity - 1)}
                      />
                      <span className="tnum w-7 text-center text-xs text-fg">{line.quantity}</span>
                      <IconButton
                        icon={Plus}
                        label={`Increase quantity of ${line.name}`}
                        size="sm"
                        variant="secondary"
                        onClick={() => void changeQuantity(line.product_id, line.quantity + 1)}
                      />
                    </div>
                    <span className="tnum text-xs font-medium text-fg">
                      {line.line_total_display}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {cart && cart.item_count > 0 && (
          <div className="shrink-0 border-t border-line p-4">
            <dl className="space-y-1 text-xs">
              <Row label="Subtotal" value={cart.subtotal_display} />
              {cart.discount_paise > 0 && (
                <Row
                  label={cart.discount_label || "Discount"}
                  value={`−${cart.discount_display}`}
                  tone="success"
                />
              )}
              {cart.tax_paise > 0 && (
                <Row label={`GST @ ${cart.tax_percent}%`} value={cart.tax_display} />
              )}
              <div className="flex items-center justify-between border-t border-line pt-2">
                <dt className="text-xs font-semibold text-fg">Total</dt>
                <dd className="tnum text-base font-semibold text-fg">{cart.total_display}</dd>
              </div>
            </dl>

            <p className="mt-2 flex items-start gap-1 text-2xs leading-relaxed text-fg-faint">
              <ShieldCheck className="mt-px h-3 w-3 shrink-0" aria-hidden />
              Calculated by the server from live catalog prices.
            </p>

            <Button
              variant="primary"
              size="lg"
              fullWidth
              icon={Zap}
              loading={preparing}
              onClick={() => void startCheckout()}
              className="mt-3"
            >
              Review &amp; pay
            </Button>
            {simulated && (
              <p className="mt-2 text-center text-2xs text-warning">
                Local sandbox — payments are simulated, no real money moves.
              </p>
            )}
          </div>
        )}
      </aside>

      <CheckoutDialog
        open={checkoutOpen}
        quote={quote}
        onClose={() => setCheckoutOpen(false)}
        onPaid={handlePaid}
        onInspectOrder={onInspectOrder}
      />
    </div>
  );
}

function Row({ label, value, tone }: { label: string; value: string; tone?: "success" }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="truncate text-fg-muted">{label}</dt>
      <dd className={cn("tnum shrink-0", tone === "success" ? "text-success" : "text-fg-muted")}>
        {value}
      </dd>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* One conversation turn                                                       */
/* -------------------------------------------------------------------------- */
function TurnView({ turn, onAdd }: { turn: Turn; onAdd: (p: Product) => void }) {
  const response = turn.response;
  const { body, note } = splitFootnote(turn.text);

  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[80%] rounded-lg rounded-br-sm bg-accent px-3 py-2 text-sm text-accent-fg">
          {turn.text}
        </p>
      </div>
    );
  }

  const products = response?.recommendations?.length
    ? response.recommendations
    : (response?.products ?? []);

  return (
    <div className="animate-fade-up space-y-3">
      <div className="flex gap-2.5">
        <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-brand-soft">
          <Bot className="h-3.5 w-3.5 text-brand" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="rounded-lg rounded-tl-sm border border-line bg-surface px-3 py-2.5">
            <RichText text={body} className="text-sm leading-relaxed text-fg" />
            {note && (
              <p className="mt-2 border-t border-line pt-2 text-2xs leading-relaxed text-fg-faint">
                {note}
              </p>
            )}
          </div>

          {response && (
            <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-fg-faint">
              <span className="font-mono">{response.intent}</span>
              <span aria-hidden>·</span>
              <span>
                {response.ai.degraded
                  ? "deterministic engine"
                  : `${response.ai.provider}/${response.ai.model}`}
              </span>
              <span aria-hidden>·</span>
              <span className="tnum">{response.ai.candidates_considered} candidates</span>
              <span aria-hidden>·</span>
              <span className="tnum">{Math.round(response.latency_ms)}ms</span>
            </p>
          )}
        </div>
      </div>

      {response?.security.catalog_injection.detected && (
        <div className="ml-9 flex items-start gap-2 rounded-md border border-warning/25 bg-warning-soft p-2.5">
          <ShieldAlert className="mt-px h-3.5 w-3.5 shrink-0 text-warning" aria-hidden />
          <p className="text-2xs leading-relaxed text-fg-muted">
            A retrieved product's text contains an instruction-injection attempt. It was fenced
            as untrusted data and had no effect on prices, policy or the confirmation
            requirement.
          </p>
        </div>
      )}

      {products.length > 0 && (
        <div className="ml-9 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {products.map((product, i) => (
            <ProductCard key={product.id} product={product} onAdd={onAdd} rank={i} />
          ))}
        </div>
      )}

      {response && response.upsell_rejected.length > 0 && (
        <details className="ml-9">
          <summary className="cursor-pointer text-2xs text-fg-faint hover:text-fg-muted">
            {response.upsell_rejected.length} add-on(s) withheld by the bounds
          </summary>
          <ul className="mt-1.5 space-y-1 border-l border-line pl-3">
            {response.upsell_rejected.map((r, i) => (
              <li key={i} className="text-2xs leading-relaxed text-fg-faint">
                <span className="text-fg-muted">{r.name ?? "Suggestion"}</span> — {r.reason}
              </li>
            ))}
          </ul>
        </details>
      )}

      {response?.checkout_error && (
        <div className="ml-9">
          <Callout tone="warning" title="Checkout is not possible yet">
            {response.message}
          </Callout>
        </div>
      )}
    </div>
  );
}

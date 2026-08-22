import { Check, Package, Plus, ShieldAlert, Sparkles } from "lucide-react";

import { Badge, Button } from "@/ui/primitives";
import type { Product, UpsellSuggestion } from "@/lib/api";
import { cn, productGradient } from "@/lib/utils";

/**
 * Splits the backend's deterministic ranking explanation into discrete reasons.
 * The string is generated server-side from catalog data, so every bullet is a
 * fact about the product rather than model prose.
 */
function reasons(why?: string): string[] {
  if (!why) return [];
  return why
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 4);
}

export function ProductCard({
  product,
  onAdd,
  busy,
  rank,
  compact,
}: {
  product: Product;
  onAdd?: (product: Product) => void;
  busy?: boolean;
  rank?: number;
  compact?: boolean;
}) {
  const isFixture = product.sku.startsWith("SEC-");
  const specs = Object.entries(product.attributes ?? {}).slice(0, 3);
  const out = product.inventory < 1;

  return (
    <article
      className={cn(
        "group flex flex-col overflow-hidden rounded-lg border bg-surface transition-colors",
        isFixture ? "border-danger/30" : "border-line hover:border-line-strong",
      )}
    >
      <div
        className="relative flex h-20 items-center justify-center"
        style={{ background: productGradient(product.sku) }}
      >
        <Package className="h-6 w-6 text-white/25" aria-hidden />
        {rank !== undefined && (
          <span className="absolute left-2 top-2 rounded bg-black/45 px-1.5 py-0.5 text-2xs font-semibold text-white backdrop-blur-sm">
            Option {String.fromCharCode(65 + rank)}
          </span>
        )}
        {product.score !== undefined && (
          <span className="tnum absolute right-2 top-2 rounded bg-black/45 px-1.5 py-0.5 text-2xs font-semibold text-white backdrop-blur-sm">
            {Math.round(product.score)}% match
          </span>
        )}
      </div>

      <div className="flex flex-1 flex-col gap-2 p-3">
        <div className="min-w-0">
          <h4 className="truncate text-xs font-semibold text-fg" title={product.name}>
            {product.name}
          </h4>
          <p className="mt-0.5 text-2xs uppercase tracking-wide text-fg-faint">
            {product.brand || product.category}
          </p>
        </div>

        {isFixture && (
          <div className="flex items-start gap-1.5 rounded border border-danger/25 bg-danger-soft p-1.5">
            <ShieldAlert className="mt-px h-3 w-3 shrink-0 text-danger" aria-hidden />
            <p className="text-2xs leading-snug text-danger">
              Security test fixture — its description holds a prompt-injection payload, handled
              as inert text.
            </p>
          </div>
        )}

        {specs.length > 0 && (
          <p className="truncate text-2xs text-fg-muted">
            {specs.map(([, v]) => String(v)).join(" · ")}
          </p>
        )}

        {!compact && !isFixture && reasons(product.why).length > 0 && (
          <ul className="space-y-0.5">
            {reasons(product.why).map((r) => (
              <li key={r} className="flex items-start gap-1 text-2xs leading-snug text-fg-muted">
                <Check className="mt-0.5 h-2.5 w-2.5 shrink-0 text-success" aria-hidden />
                <span className="line-clamp-1">{r}</span>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-auto flex items-end justify-between gap-2 pt-1">
          <div className="min-w-0">
            <p className="tnum text-sm font-semibold text-fg">{product.price_display}</p>
            {product.inventory > 0 && product.inventory <= 3 && (
              <p className="text-2xs text-warning">Only {product.inventory} left</p>
            )}
          </div>
          {onAdd && (
            <Button
              size="sm"
              variant={out ? "ghost" : "secondary"}
              icon={out ? undefined : Plus}
              disabled={busy || out}
              onClick={() => onAdd(product)}
              aria-label={out ? `${product.name} is out of stock` : `Add ${product.name} to cart`}
            >
              {out ? "Out of stock" : "Add"}
            </Button>
          )}
        </div>
      </div>
    </article>
  );
}

/**
 * A bounded add-on. The incremental cost and the resulting total are both
 * backend-computed and shown *before* the shopper agrees — that is what makes
 * the upsell bounded and explained rather than a nudge.
 */
export function UpsellCard({
  suggestion,
  onAccept,
  busy,
}: {
  suggestion: UpsellSuggestion;
  onAccept: (s: UpsellSuggestion) => void;
  busy?: boolean;
}) {
  return (
    <div className="flex items-center gap-3 rounded-md border border-line bg-surface p-2.5">
      <div
        className="grid h-9 w-9 shrink-0 place-items-center rounded"
        style={{ background: productGradient(suggestion.sku) }}
        aria-hidden
      >
        <Sparkles className="h-3.5 w-3.5 text-white/40" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <p className="truncate text-xs font-medium text-fg">{suggestion.name}</p>
          <Badge tone="brand">{suggestion.kind === "upsell" ? "upsell" : "cross-sell"}</Badge>
        </div>
        <p className="truncate text-2xs text-fg-muted" title={suggestion.reason}>
          {suggestion.reason}
        </p>
        <p className="tnum mt-0.5 text-2xs text-fg-faint">
          <span className="font-medium text-success">+{suggestion.incremental_display}</span>
          {" → new total "}
          <span className="text-fg-muted">{suggestion.new_total_display}</span>
        </p>
      </div>

      <Button size="sm" disabled={busy} onClick={() => onAccept(suggestion)}>
        Add
      </Button>
    </div>
  );
}

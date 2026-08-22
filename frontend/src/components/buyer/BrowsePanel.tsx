import { Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ProductCard } from "@/components/buyer/ProductCard";
import { FilterChips, SearchInput } from "@/ui/data";
import { EmptyState, ErrorState } from "@/ui/feedback";
import { IconButton, Skeleton } from "@/ui/primitives";
import { ApiError, api, type CategorySummary, type Product } from "@/lib/api";

/**
 * Direct catalog browsing.
 *
 * Conversation is the headline interaction, but making it the *only* way in is
 * a trap: a shopper who doesn't know what to ask has nowhere to go. This panel
 * is plain retrieval — no model involved — and adds to the same server-priced
 * cart.
 */
export function BrowsePanel({
  open,
  onClose,
  onAdd,
}: {
  open: boolean;
  onClose: () => void;
  onAdd: (product: Product) => void;
}) {
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<CategorySummary[]>([]);
  const [category, setCategory] = useState("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    api.buyer
      .products()
      .then((d) => {
        if (!alive) return;
        setProducts(d.products.filter((p) => p.active));
        setCategories(d.categories);
        setError(null);
      })
      .catch((e: ApiError) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return products.filter((p) => {
      if (category !== "all" && p.category !== category) return false;
      if (!q) return true;
      return (
        p.name.toLowerCase().includes(q) ||
        p.brand.toLowerCase().includes(q) ||
        p.category.toLowerCase().includes(q) ||
        p.tags.some((t) => t.toLowerCase().includes(q))
      );
    });
  }, [products, category, query]);

  if (!open) return null;

  return (
    <section
      aria-label="Browse catalog"
      className="flex min-h-0 shrink-0 flex-col border-b border-line bg-surface-muted/40"
    >
      <div className="flex items-center justify-between gap-3 px-4 py-2.5">
        <h3 className="text-xs font-semibold text-fg">
          Browse the catalog
          <span className="tnum ml-2 font-normal text-fg-faint">{filtered.length} items</span>
        </h3>
        <IconButton icon={X} label="Close browse panel" size="sm" onClick={onClose} />
      </div>

      <div className="flex flex-wrap items-center gap-2 px-4 pb-2.5">
        <SearchInput
          label="Filter catalog"
          value={query}
          onChange={setQuery}
          placeholder="Filter by name, brand or tag"
          className="min-w-[12rem] flex-1"
        />
        <FilterChips
          label="Category"
          value={category}
          onChange={setCategory}
          options={[
            { value: "all", label: "All", count: products.length },
            ...categories.map((c) => ({
              value: c.category,
              label: c.category,
              count: c.product_count,
            })),
          ]}
        />
      </div>

      <div className="max-h-[19rem] min-h-0 overflow-y-auto px-4 pb-4">
        {loading ? (
          <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-44" />
            ))}
          </div>
        ) : error ? (
          <ErrorState title="Unable to load the catalog" message={error} />
        ) : filtered.length === 0 ? (
          <EmptyState icon={Search} title="Nothing matches that filter" />
        ) : (
          <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {filtered.map((p) => (
              <ProductCard key={p.id} product={p} onAdd={onAdd} compact />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

import { Package, Pencil, Plus, RefreshCw, ShieldAlert, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  DataTable,
  FilterChips,
  PageHeader,
  SearchInput,
  type Column,
} from "@/ui/data";
import { Callout, EmptyState, ErrorState, useToast } from "@/ui/feedback";
import { ConfirmDialog, Drawer, Modal } from "@/ui/overlays";
import {
  Badge,
  Button,
  Card,
  Field,
  IconButton,
  Input,
  KeyValue,
  Textarea,
} from "@/ui/primitives";
import { ApiError, api, type CategorySummary, type Product, type ProductInput } from "@/lib/api";
import { cn, formatINR, productGradient } from "@/lib/utils";

type StatusFilter = "all" | "active" | "inactive" | "low_stock" | "out_of_stock";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "inactive", label: "Inactive" },
  { value: "low_stock", label: "Low stock" },
  { value: "out_of_stock", label: "Out of stock" },
];

export function CatalogPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<CategorySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [detail, setDetail] = useState<Product | null>(null);
  const [editing, setEditing] = useState<Product | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Product | null>(null);
  const [seeding, setSeeding] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const data = await api.merchant.products();
      setProducts(data.products);
      setCategories(data.categories);
      setError(null);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const byId = useMemo(() => new Map(products.map((p) => [p.id, p])), [products]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return products.filter((p) => {
      if (category !== "all" && p.category !== category) return false;
      if (status === "active" && !p.active) return false;
      if (status === "inactive" && p.active) return false;
      if (status === "low_stock" && !(p.inventory > 0 && p.inventory <= 5)) return false;
      if (status === "out_of_stock" && p.inventory !== 0) return false;
      if (!q) return true;
      return (
        p.name.toLowerCase().includes(q) ||
        p.sku.toLowerCase().includes(q) ||
        p.brand.toLowerCase().includes(q) ||
        p.tags.some((t) => t.toLowerCase().includes(q))
      );
    });
  }, [products, query, category, status]);

  async function handleDelete() {
    if (!deleting) return;
    try {
      await api.merchant.deleteProduct(deleting.id);
      toast.push("success", "Product deactivated", deleting.name);
      setDeleting(null);
      setDetail(null);
      void load();
    } catch (e) {
      toast.push("danger", "Could not deactivate", (e as ApiError).message);
    }
  }

  async function handleSeed() {
    setSeeding(true);
    try {
      const result = await api.merchant.seed(false);
      toast.push(
        "success",
        "Demo catalog loaded",
        `${result.total_products} products (${result.products_created} new).`,
      );
      void load();
    } catch (e) {
      toast.push("danger", "Seeding failed", (e as ApiError).message);
    } finally {
      setSeeding(false);
    }
  }

  if (error && products.length === 0) {
    return (
      <div className="p-4 sm:p-6">
        <ErrorState
          title="Unable to load the catalog"
          message={`The catalog service did not respond. ${error}`}
          onRetry={() => void load()}
        />
      </div>
    );
  }

  const columns: Column<Product>[] = [
    {
      key: "product",
      header: "Product",
      render: (p) => (
        <div className="flex min-w-0 items-center gap-2.5">
          <div
            className="h-7 w-7 shrink-0 rounded"
            style={{ background: productGradient(p.sku) }}
            aria-hidden
          />
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className={cn("truncate font-medium", p.active ? "text-fg" : "text-fg-faint")}>
                {p.name}
              </span>
              {p.sku.startsWith("SEC-") && (
                <ShieldAlert className="h-3 w-3 shrink-0 text-danger" aria-label="Security fixture" />
              )}
            </div>
            <span className="font-mono text-2xs text-fg-faint">{p.sku}</span>
          </div>
        </div>
      ),
    },
    {
      key: "category",
      header: "Category",
      hideBelow: "md",
      render: (p) => <span className="text-fg-muted">{p.category || "—"}</span>,
    },
    {
      key: "price",
      header: "Price",
      align: "right",
      render: (p) => <span className="tnum font-medium text-fg">{p.price_display}</span>,
    },
    {
      key: "inventory",
      header: "Stock",
      align: "right",
      render: (p) => (
        <span
          className={cn(
            "tnum",
            p.inventory === 0 ? "text-danger" : p.inventory <= 5 ? "text-warning" : "text-fg-muted",
          )}
        >
          {p.inventory}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      hideBelow: "sm",
      render: (p) =>
        p.active ? (
          <Badge tone="success">Active</Badge>
        ) : (
          <Badge tone="neutral">Inactive</Badge>
        ),
    },
    {
      key: "relations",
      header: "Linked",
      align: "right",
      hideBelow: "lg",
      render: (p) => {
        const n =
          (p.frequently_bought_together?.length ?? 0) + (p.compatible_products?.length ?? 0);
        return <span className="tnum text-fg-faint">{n || "—"}</span>;
      },
    },
    {
      key: "actions",
      header: "",
      align: "right",
      width: "5rem",
      render: (p) => (
        <div className="flex justify-end gap-0.5" onClick={(e) => e.stopPropagation()}>
          <IconButton icon={Pencil} label={`Edit ${p.name}`} size="sm" onClick={() => setEditing(p)} />
          <IconButton
            icon={Trash2}
            label={`Deactivate ${p.name}`}
            size="sm"
            disabled={!p.active}
            onClick={() => setDeleting(p)}
          />
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4 p-4 sm:p-6">
      <PageHeader
        title="Catalog"
        description="Every price the system charges is read from here at checkout time. Prices are stored as integer paise so no rounding can occur between the catalog, the cart and the payment provider."
        actions={
          <>
            <Button icon={RefreshCw} loading={seeding} onClick={() => void handleSeed()}>
              Load demo catalog
            </Button>
            <Button variant="primary" icon={Plus} onClick={() => setCreating(true)}>
              New product
            </Button>
          </>
        }
      />

      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <SearchInput
            label="Search products"
            value={query}
            onChange={setQuery}
            placeholder="Search by name, SKU, brand or tag"
            className="min-w-[14rem] flex-1"
          />
          <FilterChips
            label="Status filter"
            options={STATUS_FILTERS}
            value={status}
            onChange={setStatus}
          />
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          <FilterChips
            label="Category filter"
            value={category}
            onChange={setCategory}
            options={[
              { value: "all", label: "All categories", count: products.length },
              ...categories.map((c) => ({
                value: c.category,
                label: c.category,
                count: c.product_count,
              })),
            ]}
          />
        </div>
      </Card>

      <Card className="overflow-hidden">
        <DataTable
          caption="Product catalog"
          columns={columns}
          rows={filtered}
          loading={loading}
          getRowKey={(p) => p.id}
          onRowClick={setDetail}
          emptyState={
            products.length === 0 ? (
              <EmptyState
                icon={Package}
                title="No products in your catalog yet"
                description="Load the demo catalog to get 32 products with a real relationship graph, or create your own."
                action={
                  <Button variant="primary" loading={seeding} onClick={() => void handleSeed()}>
                    Load demo catalog
                  </Button>
                }
              />
            ) : (
              <EmptyState
                icon={Package}
                title="No products match these filters"
                description="Try a different search term, category or status."
                action={
                  <Button
                    onClick={() => {
                      setQuery("");
                      setCategory("all");
                      setStatus("all");
                    }}
                  >
                    Clear filters
                  </Button>
                }
              />
            )
          }
        />
      </Card>

      <ProductDrawer
        product={detail}
        byId={byId}
        onClose={() => setDetail(null)}
        onEdit={(p) => {
          setDetail(null);
          setEditing(p);
        }}
      />

      <ProductForm
        open={creating || editing !== null}
        product={editing}
        onClose={() => {
          setCreating(false);
          setEditing(null);
        }}
        onSaved={() => {
          setCreating(false);
          setEditing(null);
          void load();
        }}
      />

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        onConfirm={() => void handleDelete()}
        title="Deactivate product"
        destructive
        confirmLabel="Deactivate"
        message={
          <>
            <strong className="text-fg">{deleting?.name}</strong> will stop appearing in search,
            recommendations and checkout. Existing orders keep referencing it, so nothing is
            deleted and no history is lost.
          </>
        }
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Detail drawer                                                               */
/* -------------------------------------------------------------------------- */
function ProductDrawer({
  product,
  byId,
  onClose,
  onEdit,
}: {
  product: Product | null;
  byId: Map<string, Product>;
  onClose: () => void;
  onEdit: (p: Product) => void;
}) {
  if (!product) return null;

  const relations: { label: string; ids: string[]; hint: string }[] = [
    {
      label: "Frequently bought together",
      ids: product.frequently_bought_together ?? [],
      hint: "Drives cross-sell suggestions.",
    },
    {
      label: "Compatible products",
      ids: product.compatible_products ?? [],
      hint: "Drives upsell suggestions.",
    },
  ];

  return (
    <Drawer
      open
      onClose={onClose}
      title={product.name}
      description={product.sku}
      footer={
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Close</Button>
          <Button variant="primary" icon={Pencil} onClick={() => onEdit(product)}>
            Edit product
          </Button>
        </div>
      }
    >
      <div className="space-y-5">
        <div
          className="flex h-24 items-center justify-center rounded-lg"
          style={{ background: productGradient(product.sku) }}
        >
          <Package className="h-8 w-8 text-white/30" aria-hidden />
        </div>

        {product.sku.startsWith("SEC-") && (
          <Callout tone="danger" title="Security test fixture">
            This product's description contains a live prompt-injection payload. It is stored
            as ordinary catalog data and is inert — the agent treats it as text, and policy,
            pricing, confirmation and payment verification never read model output.
          </Callout>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Stat label="Price" value={product.price_display} large />
          <Stat
            label="Inventory"
            value={String(product.inventory)}
            large
            tone={product.inventory === 0 ? "danger" : product.inventory <= 5 ? "warning" : "normal"}
          />
        </div>

        <section>
          <h4 className="mb-1.5 text-xs font-semibold text-fg">Description</h4>
          <p className="text-xs leading-relaxed text-fg-muted">
            {product.description || "No description."}
          </p>
        </section>

        <section>
          <h4 className="mb-1.5 text-xs font-semibold text-fg">Details</h4>
          <dl className="rounded-md border border-line px-3 py-1">
            <KeyValue label="Category">{product.category || "—"}</KeyValue>
            <KeyValue label="Subcategory">{product.subcategory || "—"}</KeyValue>
            <KeyValue label="Brand">{product.brand || "—"}</KeyValue>
            <KeyValue label="Currency">{product.currency}</KeyValue>
            <KeyValue label="Price (paise)" mono>
              {product.price_paise.toLocaleString()}
            </KeyValue>
            <KeyValue label="Status">{product.active ? "Active" : "Inactive"}</KeyValue>
          </dl>
        </section>

        {Object.keys(product.attributes ?? {}).length > 0 && (
          <section>
            <h4 className="mb-1.5 text-xs font-semibold text-fg">Attributes</h4>
            <dl className="rounded-md border border-line px-3 py-1">
              {Object.entries(product.attributes).map(([k, v]) => (
                <KeyValue key={k} label={k}>
                  {String(v)}
                </KeyValue>
              ))}
            </dl>
          </section>
        )}

        {product.tags.length > 0 && (
          <section>
            <h4 className="mb-1.5 text-xs font-semibold text-fg">Tags</h4>
            <div className="flex flex-wrap gap-1">
              {product.tags.map((t) => (
                <Badge key={t}>{t}</Badge>
              ))}
            </div>
          </section>
        )}

        <section>
          <h4 className="mb-1 text-xs font-semibold text-fg">Relationships</h4>
          <p className="mb-2 text-2xs leading-relaxed text-fg-muted">
            Add-on suggestions are drawn only from these curated links. The agent never infers
            compatibility on its own.
          </p>
          <div className="space-y-3">
            {relations.map((rel) => (
              <div key={rel.label}>
                <p className="mb-1 text-2xs font-medium uppercase tracking-wide text-fg-faint">
                  {rel.label} · {rel.hint}
                </p>
                {rel.ids.length === 0 ? (
                  <p className="text-2xs text-fg-faint">None linked.</p>
                ) : (
                  <ul className="space-y-1">
                    {rel.ids.map((id) => {
                      const linked = byId.get(id);
                      return (
                        <li
                          key={id}
                          className="flex items-center justify-between gap-2 rounded border border-line px-2 py-1.5"
                        >
                          <span className="truncate text-2xs text-fg">
                            {linked?.name ?? id}
                          </span>
                          <span className="tnum shrink-0 text-2xs text-fg-muted">
                            {linked?.price_display ?? ""}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </section>
      </div>
    </Drawer>
  );
}

function Stat({
  label,
  value,
  large,
  tone = "normal",
}: {
  label: string;
  value: string;
  large?: boolean;
  tone?: "normal" | "warning" | "danger";
}) {
  return (
    <div className="rounded-md border border-line p-3">
      <p className="text-2xs uppercase tracking-wide text-fg-faint">{label}</p>
      <p
        className={cn(
          "tnum mt-1 font-semibold",
          large ? "text-lg" : "text-sm",
          tone === "warning" && "text-warning",
          tone === "danger" && "text-danger",
          tone === "normal" && "text-fg",
        )}
      >
        {value}
      </p>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Create / edit                                                               */
/* -------------------------------------------------------------------------- */
function blank(): ProductInput {
  return {
    sku: "",
    name: "",
    description: "",
    category: "",
    subcategory: "",
    brand: "",
    price_paise: 0,
    inventory: 0,
    tags: [],
    active: true,
  };
}

function ProductForm({
  open,
  product,
  onClose,
  onSaved,
}: {
  open: boolean;
  product: Product | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<ProductInput>(blank());
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const toast = useToast();

  useEffect(() => {
    if (!open) return;
    setErrors({});
    setForm(
      product
        ? {
            sku: product.sku,
            name: product.name,
            description: product.description,
            category: product.category,
            subcategory: product.subcategory,
            brand: product.brand,
            price_paise: product.price_paise,
            inventory: product.inventory,
            tags: product.tags,
            active: product.active,
          }
        : blank(),
    );
  }, [open, product]);

  function validate(): boolean {
    const next: Record<string, string> = {};
    if (!form.sku.trim()) next.sku = "A SKU is required.";
    if (!form.name.trim()) next.name = "A product name is required.";
    if (form.price_paise < 0) next.price_paise = "Price cannot be negative.";
    if ((form.inventory ?? 0) < 0) next.inventory = "Inventory cannot be negative.";
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function save() {
    if (!validate()) return;
    setSaving(true);
    try {
      if (product) {
        await api.merchant.updateProduct(product.id, form);
        toast.push("success", "Product updated", form.name);
      } else {
        await api.merchant.createProduct(form);
        toast.push("success", "Product created", form.name);
      }
      onSaved();
    } catch (e) {
      const err = e as ApiError;
      if (err.code === "duplicate_sku") setErrors({ sku: err.message });
      else toast.push("danger", "Could not save the product", err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={product ? `Edit ${product.name}` : "New product"}
      description="Prices are entered in paise — 1 rupee is 100 paise."
      footer={
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={saving} onClick={() => void save()}>
            {product ? "Save changes" : "Create product"}
          </Button>
        </div>
      }
    >
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="SKU" htmlFor="sku" error={errors.sku}>
            <Input
              id="sku"
              value={form.sku}
              onChange={(e) => setForm({ ...form, sku: e.target.value })}
              placeholder="LAP-DEV-001"
            />
          </Field>
          <Field label="Brand" htmlFor="brand">
            <Input
              id="brand"
              value={form.brand ?? ""}
              onChange={(e) => setForm({ ...form, brand: e.target.value })}
            />
          </Field>
        </div>

        <Field label="Product name" htmlFor="name" error={errors.name}>
          <Input
            id="name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </Field>

        <Field label="Description" htmlFor="description">
          <Textarea
            id="description"
            rows={3}
            value={form.description ?? ""}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </Field>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Category" htmlFor="category">
            <Input
              id="category"
              value={form.category ?? ""}
              onChange={(e) => setForm({ ...form, category: e.target.value })}
            />
          </Field>
          <Field label="Subcategory" htmlFor="subcategory">
            <Input
              id="subcategory"
              value={form.subcategory ?? ""}
              onChange={(e) => setForm({ ...form, subcategory: e.target.value })}
            />
          </Field>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label="Price in paise"
            htmlFor="price"
            error={errors.price_paise}
            hint={`Displays as ${formatINR(form.price_paise || 0)}`}
          >
            <Input
              id="price"
              type="number"
              min={0}
              value={form.price_paise}
              onChange={(e) => setForm({ ...form, price_paise: Number(e.target.value) })}
            />
          </Field>
          <Field label="Inventory" htmlFor="inventory" error={errors.inventory}>
            <Input
              id="inventory"
              type="number"
              min={0}
              value={form.inventory ?? 0}
              onChange={(e) => setForm({ ...form, inventory: Number(e.target.value) })}
            />
          </Field>
        </div>

        <Field
          label="Tags"
          htmlFor="tags"
          hint="Comma separated. Tags drive use-case matching, e.g. programming, portable, office."
        >
          <Input
            id="tags"
            value={(form.tags ?? []).join(", ")}
            onChange={(e) =>
              setForm({
                ...form,
                tags: e.target.value
                  .split(",")
                  .map((t) => t.trim())
                  .filter(Boolean),
              })
            }
          />
        </Field>
      </div>
    </Modal>
  );
}

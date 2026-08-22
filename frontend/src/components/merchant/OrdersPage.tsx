import { FileSearch, Receipt, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { DataTable, FilterChips, PageHeader, SearchInput, type Column } from "@/ui/data";
import { Callout, EmptyState, ErrorState, StatusBadge, SyntheticBadge, useToast } from "@/ui/feedback";
import { Drawer } from "@/ui/overlays";
import { Badge, Button, Card, KeyValue, Skeleton } from "@/ui/primitives";
import { ApiError, api, type ActivitySession, type OrderDetail } from "@/lib/api";
import { cn, relativeTime, shortId } from "@/lib/utils";

type StatusFilter = "all" | "PAID" | "PAYMENT_FAILED" | "PAYMENT_PENDING" | "CHECKOUT_PENDING" | "CART";

const FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "PAID", label: "Paid" },
  { value: "PAYMENT_FAILED", label: "Failed" },
  { value: "PAYMENT_PENDING", label: "Pending" },
  { value: "CHECKOUT_PENDING", label: "Awaiting approval" },
  { value: "CART", label: "Cart" },
];

/**
 * Orders are derived from the session activity feed — the backend exposes
 * orders per session rather than a flat list, and inventing a new endpoint
 * would mean changing working backend code for a presentational convenience.
 */
export function OrdersPage({ onInspectOrder }: { onInspectOrder: (id: string) => void }) {
  const [sessions, setSessions] = useState<ActivitySession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [query, setQuery] = useState("");
  // `?order=` deep-links straight to one order's detail panel.
  const [detailId, setDetailId] = useState<string | null>(
    () => new URLSearchParams(window.location.search).get("order"),
  );

  const load = useCallback(async () => {
    try {
      const data = await api.merchant.activity(100);
      setSessions(data.sessions.filter((s) => s.order_id));
      setError(null);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, [load]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return sessions.filter((s) => {
      if (status !== "all" && s.order_status !== status) return false;
      if (!q) return true;
      return (
        (s.order_id ?? "").toLowerCase().includes(q) ||
        s.session_id.toLowerCase().includes(q) ||
        s.actor_label.toLowerCase().includes(q) ||
        (s.payment_id ?? "").toLowerCase().includes(q)
      );
    });
  }, [sessions, status, query]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const s of sessions) c[s.order_status ?? ""] = (c[s.order_status ?? ""] ?? 0) + 1;
    return c;
  }, [sessions]);

  if (error && sessions.length === 0) {
    return (
      <div className="p-4 sm:p-6">
        <ErrorState
          title="Unable to load orders"
          message={`The orders service did not respond. ${error}`}
          onRetry={() => void load()}
        />
      </div>
    );
  }

  const columns: Column<ActivitySession>[] = [
    {
      key: "order",
      header: "Order",
      render: (s) => (
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="font-mono text-xs text-fg">{shortId(s.order_id, 6)}</span>
            {s.is_synthetic && <SyntheticBadge />}
          </div>
          <span className="text-2xs text-fg-faint">{s.actor_label}</span>
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (s) => <StatusBadge status={s.order_status} />,
    },
    {
      key: "payment",
      header: "Payment ID",
      hideBelow: "lg",
      render: (s) =>
        s.payment_id ? (
          <span className="font-mono text-2xs text-fg-muted">{shortId(s.payment_id, 8)}</span>
        ) : (
          <span className="text-2xs text-fg-faint">—</span>
        ),
    },
    {
      key: "items",
      header: "Items",
      align: "right",
      hideBelow: "sm",
      render: (s) => <span className="tnum text-fg-muted">{s.items}</span>,
    },
    {
      key: "total",
      header: "Amount",
      align: "right",
      render: (s) => <span className="tnum font-medium text-fg">{s.total_display}</span>,
    },
    {
      key: "when",
      header: "Created",
      align: "right",
      hideBelow: "md",
      render: (s) => <span className="text-fg-faint">{relativeTime(s.created_at)}</span>,
    },
  ];

  return (
    <div className="space-y-4 p-4 sm:p-6">
      <PageHeader
        title="Orders"
        description="Every order, its payment state and whether that payment was verified server-side. An order reaches Paid only after the provider confirms a captured payment for the exact approved amount."
        actions={
          <Button icon={RefreshCw} onClick={() => void load()}>
            Refresh
          </Button>
        }
      />

      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <SearchInput
            label="Search orders"
            value={query}
            onChange={setQuery}
            placeholder="Search by order, session, payment id or buyer"
            className="min-w-[16rem] flex-1"
          />
          <FilterChips
            label="Order status"
            options={FILTERS.map((f) => ({
              ...f,
              count: f.value === "all" ? sessions.length : counts[f.value],
            }))}
            value={status}
            onChange={setStatus}
          />
        </div>
      </Card>

      <Card className="overflow-hidden">
        <DataTable
          caption="Orders"
          columns={columns}
          rows={rows}
          loading={loading}
          getRowKey={(s) => s.order_id ?? s.session_id}
          onRowClick={(s) => s.order_id && setDetailId(s.order_id)}
          emptyState={
            sessions.length === 0 ? (
              <EmptyState
                icon={Receipt}
                title="No transactions yet"
                description="Orders appear here as soon as a buyer reaches checkout in the AI buyer interface."
              />
            ) : (
              <EmptyState
                icon={Receipt}
                title="No orders match these filters"
                action={
                  <Button
                    onClick={() => {
                      setQuery("");
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

      <OrderDrawer
        orderId={detailId}
        onClose={() => setDetailId(null)}
        onInspectAudit={(id) => {
          setDetailId(null);
          onInspectOrder(id);
        }}
      />
    </div>
  );
}

function OrderDrawer({
  orderId,
  onClose,
  onInspectAudit,
}: {
  orderId: string | null;
  onClose: () => void;
  onInspectAudit: (id: string) => void;
}) {
  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const toast = useToast();

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      setOrder(await api.payments.order(id));
    } catch {
      setOrder(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!orderId) {
      setOrder(null);
      return;
    }
    void load(orderId);
  }, [orderId, load]);

  async function reconcile() {
    if (!orderId) return;
    setReconciling(true);
    try {
      const result = await api.payments.reconcile(orderId);
      toast.push(
        result.paid ? "success" : "info",
        result.paid ? "Reconciled — payment confirmed" : "Reconciliation complete",
        result.message,
      );
      void load(orderId);
    } catch (e) {
      toast.push("warning", "Could not reconcile", (e as ApiError).message);
    } finally {
      setReconciling(false);
    }
  }

  if (!orderId) return null;

  const latest = order?.transactions.at(-1);

  return (
    <Drawer
      open
      onClose={onClose}
      title={loading ? "Loading order…" : `Order ${shortId(order?.order_id, 8)}`}
      description={order ? `${order.items.length} item(s) · ${order.total_display}` : undefined}
      footer={
        order && (
          <div className="flex flex-wrap justify-end gap-2">
            {!order.paid && (
              <Button icon={RefreshCw} loading={reconciling} onClick={() => void reconcile()}>
                Re-check payment status
              </Button>
            )}
            <Button icon={FileSearch} variant="primary" onClick={() => onInspectAudit(order.order_id)}>
              View audit story
            </Button>
          </div>
        )
      }
    >
      {loading || !order ? (
        <div className="space-y-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-32" />
          <Skeleton className="h-24" />
        </div>
      ) : (
        <div className="space-y-5">
          <div
            className={cn(
              "rounded-md border p-3",
              order.paid ? "border-success/25 bg-success-soft" : "border-line bg-surface-muted",
            )}
          >
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-2xs uppercase tracking-wide text-fg-muted">Amount</p>
                <p className="tnum text-xl font-semibold text-fg">{order.total_display}</p>
                <p className="text-2xs text-fg-faint">{order.currency}</p>
              </div>
              <div className="text-right">
                <StatusBadge status={order.status} />
                {order.is_synthetic && (
                  <div className="mt-1">
                    <SyntheticBadge />
                  </div>
                )}
              </div>
            </div>
          </div>

          {!order.paid && (
            <Callout tone="warning" title="This order is not marked paid">
              No verified payment exists for it. Reconciling asks the provider directly and will
              move it to paid only if a matching captured payment genuinely exists.
            </Callout>
          )}

          <section>
            <h4 className="mb-1.5 text-xs font-semibold text-fg">Items</h4>
            <div className="overflow-hidden rounded-md border border-line">
              <table className="w-full text-xs">
                <tbody className="divide-y divide-line">
                  {order.items.map((i) => (
                    <tr key={i.product_id}>
                      <td className="px-3 py-2">
                        <span className="text-fg">{i.name}</span>
                        {i.source !== "direct" && (
                          <Badge tone="brand" className="ml-1.5">
                            {i.source === "upsell" ? "upsell" : "cross-sell"}
                          </Badge>
                        )}
                      </td>
                      <td className="tnum px-3 py-2 text-right text-fg-muted">
                        {i.quantity} × {i.unit_price_display}
                      </td>
                      <td className="tnum px-3 py-2 text-right font-medium text-fg">
                        {i.line_total_display}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <dl className="mt-2 rounded-md border border-line px-3 py-1">
              <KeyValue label="Subtotal" mono>{order.subtotal_display}</KeyValue>
              <KeyValue label="Discount" mono>{order.discount_display}</KeyValue>
              <KeyValue label="Tax" mono>{order.tax_display}</KeyValue>
              <KeyValue label="Total" mono>
                <span className="font-semibold">{order.total_display}</span>
              </KeyValue>
            </dl>
          </section>

          <section>
            <h4 className="mb-1.5 text-xs font-semibold text-fg">Payment</h4>
            <dl className="rounded-md border border-line px-3 py-1">
              <KeyValue label="Provider" mono>{order.payment_provider ?? "—"}</KeyValue>
              <KeyValue label="Provider order" mono>{order.payment_order_id ?? "—"}</KeyValue>
              <KeyValue label="Payment id" mono>{order.payment_id ?? "—"}</KeyValue>
              <KeyValue label="Payment status">
                <StatusBadge status={latest?.status} />
              </KeyValue>
              <KeyValue label="Verification">
                <StatusBadge status={latest?.verification_status} />
              </KeyValue>
              <KeyValue label="Signature checked" mono>
                {latest ? String(latest.signature_present) : "—"}
              </KeyValue>
              <KeyValue label="Test mode" mono>
                {latest ? String(latest.is_test_mode) : "true"}
              </KeyValue>
            </dl>
            {latest?.failure_reason && (
              <Callout tone="danger" className="mt-2" title="Failure reason">
                {latest.failure_reason}
              </Callout>
            )}
          </section>

          {order.transactions.length > 1 && (
            <section>
              <h4 className="mb-1.5 text-xs font-semibold text-fg">
                Payment attempts ({order.transactions.length})
              </h4>
              <ul className="space-y-1.5">
                {order.transactions.map((t) => (
                  <li
                    key={t.transaction_id}
                    className="flex items-center justify-between gap-2 rounded border border-line px-2.5 py-2"
                  >
                    <div className="min-w-0">
                      <p className="font-mono text-2xs text-fg">{shortId(t.provider_order_id, 8)}</p>
                      <p className="text-2xs text-fg-faint">{relativeTime(t.created_at)}</p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <span className="tnum text-2xs text-fg-muted">{t.amount_display}</span>
                      <StatusBadge status={t.status} />
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </Drawer>
  );
}

import {
  Activity,
  AlertTriangle,
  IndianRupee,
  Package,
  Percent,
  ShoppingCart,
  Sparkles,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import type { RouteId } from "@/components/shell/AppShell";
import {
  ChartCard,
  DataTable,
  FilterChips,
  MetricCard,
  PageHeader,
  SectionTitle,
  StackedBar,
  type Column,
} from "@/ui/data";
import {
  Callout,
  EmptyState,
  ErrorState,
  StatusBadge,
  SyntheticBadge,
} from "@/ui/feedback";
import { Badge, Button, Card, Skeleton } from "@/ui/primitives";
import { ApiError, api, type ActivitySession, type Overview } from "@/lib/api";
import { cn, formatINR, relativeTime } from "@/lib/utils";

type Scope = "live" | "synthetic" | "combined";

const SCOPES: { value: Scope; label: string }[] = [
  { value: "live", label: "Live" },
  { value: "synthetic", label: "Synthetic" },
  { value: "combined", label: "Combined" },
];

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

export function OverviewPage({
  onInspectOrder,
  onNavigate,
}: {
  onInspectOrder: (orderId: string) => void;
  onNavigate: (r: RouteId) => void;
}) {
  const [data, setData] = useState<Overview | null>(null);
  const [sessions, setSessions] = useState<ActivitySession[]>([]);
  const [scope, setScope] = useState<Scope>("live");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (showSpinner = false) => {
    if (showSpinner) setLoading(true);
    try {
      const [overview, activity] = await Promise.all([
        api.merchant.overview(),
        api.merchant.activity(25),
      ]);
      setData(overview);
      setSessions(activity.sessions);
      setError(null);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(true);
    const timer = setInterval(() => void load(), 10000);
    return () => clearInterval(timer);
  }, [load]);

  const metrics = data?.[scope] ?? null;

  if (error && !data) {
    return (
      <div className="p-4 sm:p-6">
        <ErrorState
          title="Unable to load the dashboard"
          message={`The metrics service did not respond. ${error}`}
          onRetry={() => void load(true)}
        />
      </div>
    );
  }

  const statusSegments = [
    { label: "Paid", value: metrics?.paid_orders ?? 0, className: "bg-success" },
    {
      label: "Failed",
      value: metrics?.failed_payments ?? 0,
      className: "bg-danger",
    },
    {
      label: "Abandoned",
      value: metrics?.abandoned_carts ?? 0,
      className: "bg-warning",
    },
  ];

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title={`${greeting()}, merchant`}
        description="Monitor commerce performance, AI-assisted growth and payment health. Every figure is computed from activity this application recorded — nothing here is hardcoded."
        actions={
          <FilterChips
            label="Data scope"
            options={SCOPES}
            value={scope}
            onChange={setScope}
          />
        }
      />

      {metrics?.is_synthetic && (
        <Callout tone="warning" title="Synthetic / demo data">
          These rows were produced by the growth simulator, not by real shoppers. They are
          never mixed into live figures.
        </Callout>
      )}

      {/* ---------------------------------------------------------- KPIs */}
      {!loading && metrics && metrics.sessions === 0 ? (
        <Card>
          <EmptyState
            icon={Activity}
            title="No activity in this scope yet"
            description={
              scope === "synthetic"
                ? "Run the growth simulation to generate labelled synthetic sessions and a baseline comparison."
                : "Open the AI Buyer and complete a purchase. These numbers are computed from real activity, so they start at zero."
            }
            action={
              <Button
                variant="primary"
                onClick={() => onNavigate(scope === "synthetic" ? "growth" : "buyer")}
              >
                {scope === "synthetic" ? "Go to Growth" : "Open AI Buyer"}
              </Button>
            }
          />
        </Card>
      ) : (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <MetricCard
            label={metrics?.is_synthetic ? "Synthetic GMV" : "Test GMV"}
            value={metrics?.gmv_display ?? "—"}
            hint={`${metrics?.paid_orders ?? 0} paid order${metrics?.paid_orders === 1 ? "" : "s"}`}
            icon={IndianRupee}
            emphasis="success"
            loading={loading}
          />
          <MetricCard
            label="Average order value"
            value={metrics?.aov_display ?? "—"}
            hint={`Revenue/session ${metrics?.revenue_per_session_display ?? "—"}`}
            icon={ShoppingCart}
            loading={loading}
          />
          <MetricCard
            label="Conversion"
            value={metrics ? `${metrics.conversion_rate_percent}%` : "—"}
            hint={`${metrics?.converting_sessions ?? 0} of ${metrics?.sessions ?? 0} sessions`}
            icon={Percent}
            loading={loading}
          />
          <MetricCard
            label="Add-on revenue"
            value={
              metrics
                ? formatINR(metrics.upsell_revenue_paise + metrics.cross_sell_revenue_paise)
                : "—"
            }
            hint={`${metrics?.addon_acceptance_rate_percent ?? 0}% of offers accepted`}
            icon={Sparkles}
            emphasis="success"
            loading={loading}
          />
        </div>
      )}

      {/* ------------------------------------------------------ analytics */}
      {metrics && metrics.sessions > 0 && (
        <div className="grid gap-3 lg:grid-cols-3">
          <ChartCard
            title="Order outcomes"
            description="Where sessions ended up"
            scope={metrics.label}
            className="lg:col-span-1"
          >
            {loading ? (
              <Skeleton className="h-16 w-full" />
            ) : (
              <StackedBar
                segments={statusSegments}
                total={statusSegments.reduce((a, s) => a + s.value, 0)}
              />
            )}
          </ChartCard>

          <Card className="p-4 lg:col-span-2">
            <SectionTitle title="Commerce health" description="Secondary indicators" />
            <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
              <MiniStat
                label="Sessions"
                value={String(metrics.sessions)}
                icon={Users}
                loading={loading}
              />
              <MiniStat
                label="Orders created"
                value={String(metrics.orders_created)}
                icon={Package}
                loading={loading}
              />
              <MiniStat
                label="Cart abandonment"
                value={`${metrics.cart_abandonment_rate_percent}%`}
                tone={metrics.cart_abandonment_rate_percent > 50 ? "warning" : "normal"}
                loading={loading}
              />
              <MiniStat
                label="Failed payments"
                value={String(metrics.failed_payments)}
                icon={AlertTriangle}
                tone={metrics.failed_payments > 0 ? "danger" : "normal"}
                hint={`${metrics.payment_failure_rate_percent}% of attempts`}
                loading={loading}
              />
              <MiniStat
                label="Upsell revenue"
                value={metrics.upsell_revenue_display}
                loading={loading}
              />
              <MiniStat
                label="Cross-sell revenue"
                value={metrics.cross_sell_revenue_display}
                loading={loading}
              />
              <MiniStat
                label="Discounts given"
                value={metrics.discount_given_display}
                loading={loading}
              />
              <MiniStat
                label="Revenue / session"
                value={metrics.revenue_per_session_display}
                loading={loading}
              />
            </div>
          </Card>
        </div>
      )}

      {/* -------------------------------------------------- live activity */}
      <div>
        <SectionTitle
          title="Live activity"
          description="Buyer sessions, what they asked for, and where each one reached."
          action={
            <Button size="sm" variant="ghost" onClick={() => onNavigate("audit")}>
              Open audit explorer
            </Button>
          }
        />
        <Card className="overflow-hidden">
          <ActivityTable
            sessions={sessions}
            loading={loading}
            onInspectOrder={onInspectOrder}
            onOpenBuyer={() => onNavigate("buyer")}
          />
        </Card>
      </div>

      {data && (
        <p className="text-2xs leading-relaxed text-fg-faint">{data.disclaimer}</p>
      )}
    </div>
  );
}

function MiniStat({
  label,
  value,
  hint,
  icon: Icon,
  tone = "normal",
  loading,
}: {
  label: string;
  value: string;
  hint?: string;
  icon?: React.ComponentType<{ className?: string }>;
  tone?: "normal" | "warning" | "danger";
  loading?: boolean;
}) {
  return (
    <div>
      <p className="flex items-center gap-1 text-2xs uppercase tracking-wide text-fg-faint">
        {Icon && <Icon className="h-3 w-3" aria-hidden />}
        {label}
      </p>
      {loading ? (
        <Skeleton className="mt-1.5 h-5 w-16" />
      ) : (
        <p
          className={cn(
            "tnum mt-1 text-base font-semibold",
            tone === "warning" && "text-warning",
            tone === "danger" && "text-danger",
            tone === "normal" && "text-fg",
          )}
        >
          {value}
        </p>
      )}
      {hint && <p className="mt-0.5 text-2xs text-fg-faint">{hint}</p>}
    </div>
  );
}

function ActivityTable({
  sessions,
  loading,
  onInspectOrder,
  onOpenBuyer,
}: {
  sessions: ActivitySession[];
  loading: boolean;
  onInspectOrder: (id: string) => void;
  onOpenBuyer: () => void;
}) {
  const columns: Column<ActivitySession>[] = [
    {
      key: "actor",
      header: "Buyer",
      render: (s) => (
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-medium text-fg">{s.actor_label}</span>
          {s.is_synthetic && <SyntheticBadge />}
          {s.actor_type === "ai_agent" && !s.is_synthetic && <Badge tone="brand">Agent</Badge>}
        </div>
      ),
    },
    {
      key: "intent",
      header: "Intent",
      hideBelow: "md",
      render: (s) => (
        <span className="line-clamp-1 max-w-[26rem] text-fg-muted" title={s.last_intent}>
          {s.last_intent || "—"}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (s) => <StatusBadge status={s.order_status} />,
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
      header: "Total",
      align: "right",
      render: (s) => <span className="tnum font-medium text-fg">{s.total_display}</span>,
    },
    {
      key: "when",
      header: "When",
      align: "right",
      hideBelow: "sm",
      render: (s) => <span className="text-fg-faint">{relativeTime(s.created_at)}</span>,
    },
  ];

  return (
    <DataTable
      caption="Recent buyer sessions"
      columns={columns}
      rows={sessions}
      loading={loading}
      getRowKey={(s) => s.session_id}
      onRowClick={(s) => s.order_id && onInspectOrder(s.order_id)}
      emptyState={
        <EmptyState
          icon={Users}
          title="No buyer sessions yet"
          description="Activity appears here as soon as someone shops through the AI buyer."
          action={
            <Button variant="primary" onClick={onOpenBuyer}>
              Open AI Buyer
            </Button>
          }
        />
      }
    />
  );
}

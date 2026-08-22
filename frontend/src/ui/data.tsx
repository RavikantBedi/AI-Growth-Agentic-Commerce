/** Metric cards, tables, filters and charts. Charts are hand-rolled SVG so the
 *  project takes on no charting dependency. */
import { ArrowDownRight, ArrowRight, ArrowUpRight, Search } from "lucide-react";
import { type ReactNode } from "react";

import { Card, Input, Skeleton } from "@/ui/primitives";
import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/* Metric card                                                                 */
/* -------------------------------------------------------------------------- */
export function MetricCard({
  label,
  value,
  hint,
  delta,
  deltaLabel,
  loading,
  emphasis = "normal",
  icon: Icon,
  onClick,
}: {
  label: string;
  value: string;
  hint?: ReactNode;
  /** Percentage change. `null`/undefined renders "Not enough data" — never a fabricated trend. */
  delta?: number | null;
  deltaLabel?: string;
  loading?: boolean;
  emphasis?: "normal" | "success" | "danger";
  icon?: React.ComponentType<{ className?: string }>;
  onClick?: () => void;
}) {
  const Tag = onClick ? "button" : "div";
  return (
    <Card
      as={Tag}
      interactive={Boolean(onClick)}
      onClick={onClick}
      className={cn("p-4 text-left", onClick && "w-full cursor-pointer")}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="truncate text-2xs font-medium uppercase tracking-wide text-fg-muted">
          {label}
        </p>
        {Icon && <Icon className="h-3.5 w-3.5 shrink-0 text-fg-faint" aria-hidden />}
      </div>

      {loading ? (
        <Skeleton className="mt-2.5 h-7 w-28" />
      ) : (
        <p
          className={cn(
            "tnum mt-2 text-2xl font-semibold tracking-tight",
            emphasis === "success" && "text-success",
            emphasis === "danger" && "text-danger",
            emphasis === "normal" && "text-fg",
          )}
        >
          {value}
        </p>
      )}

      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
        {delta !== undefined && !loading && <DeltaPill value={delta} label={deltaLabel} />}
        {hint && <span className="text-2xs text-fg-faint">{hint}</span>}
      </div>
    </Card>
  );
}

/** Renders a real change, or says there isn't enough data. Never invents one. */
export function DeltaPill({ value, label }: { value: number | null; label?: string }) {
  if (value === null || Number.isNaN(value)) {
    return <span className="text-2xs text-fg-faint">Not enough data</span>;
  }
  const flat = Math.abs(value) < 0.005;
  const up = value > 0;
  const Icon = flat ? ArrowRight : up ? ArrowUpRight : ArrowDownRight;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-2xs font-medium tnum",
        flat ? "bg-surface-muted text-fg-muted" : up ? "bg-success-soft text-success" : "bg-danger-soft text-danger",
      )}
    >
      <Icon className="h-3 w-3" aria-hidden />
      {flat ? "No change" : `${up ? "+" : ""}${value.toFixed(1)}%`}
      {label && <span className="ml-0.5 font-normal opacity-70">{label}</span>}
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* Page header                                                                 */
/* -------------------------------------------------------------------------- */
export function PageHeader({
  title,
  description,
  actions,
  className,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-3", className)}>
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-fg">{title}</h1>
        {description && (
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-fg-muted">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function SectionTitle({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-3 flex flex-wrap items-end justify-between gap-3", className)}>
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-fg">{title}</h2>
        {description && <p className="mt-0.5 text-xs text-fg-muted">{description}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Filter bar                                                                  */
/* -------------------------------------------------------------------------- */
export function SearchInput({
  value,
  onChange,
  placeholder = "Search",
  label,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  label: string;
  className?: string;
}) {
  return (
    <div className={cn("relative", className)}>
      <Search
        className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-faint"
        aria-hidden
      />
      <Input
        type="search"
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="pl-8"
      />
    </div>
  );
}

export function FilterChips<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: string; count?: number }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          aria-pressed={value === o.value}
          className={cn(
            "inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 text-xs transition-colors",
            value === o.value
              ? "border-accent bg-accent text-accent-fg"
              : "border-line bg-surface text-fg-muted hover:border-line-strong hover:text-fg",
          )}
        >
          {o.label}
          {o.count !== undefined && (
            <span className="tnum text-2xs opacity-60">{o.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Table                                                                       */
/* -------------------------------------------------------------------------- */
export interface Column<T> {
  key: string;
  header: string;
  /** Right-align numeric columns so figures line up. */
  align?: "left" | "right";
  /** Hide below the given breakpoint to keep mobile readable. */
  hideBelow?: "sm" | "md" | "lg";
  width?: string;
  render: (row: T) => ReactNode;
}

export function DataTable<T>({
  columns,
  rows,
  getRowKey,
  onRowClick,
  emptyState,
  loading,
  caption,
}: {
  columns: Column<T>[];
  rows: T[];
  getRowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  emptyState?: ReactNode;
  loading?: boolean;
  caption?: string;
}) {
  const hideClass = (b?: "sm" | "md" | "lg") =>
    b === "sm" ? "hidden sm:table-cell" : b === "md" ? "hidden md:table-cell" : b === "lg" ? "hidden lg:table-cell" : "";

  if (loading) {
    return (
      <div className="divide-y divide-line" aria-busy="true">
        {Array.from({ length: 6 }).map((_, r) => (
          <div key={r} className="flex items-center gap-4 px-4 py-3">
            {columns.slice(0, 5).map((c, i) => (
              <Skeleton key={c.key} className={cn("h-3", i === 0 ? "w-1/4" : "flex-1")} />
            ))}
          </div>
        ))}
      </div>
    );
  }

  if (rows.length === 0) return <>{emptyState}</>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr className="border-b border-line bg-surface-muted/60">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                style={c.width ? { width: c.width } : undefined}
                className={cn(
                  "whitespace-nowrap px-4 py-2.5 font-medium text-fg-muted",
                  c.align === "right" ? "text-right" : "text-left",
                  hideClass(c.hideBelow),
                )}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((row) => {
            const clickable = Boolean(onRowClick);
            return (
              <tr
                key={getRowKey(row)}
                onClick={clickable ? () => onRowClick?.(row) : undefined}
                onKeyDown={
                  clickable
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onRowClick?.(row);
                        }
                      }
                    : undefined
                }
                tabIndex={clickable ? 0 : undefined}
                role={clickable ? "button" : undefined}
                className={cn(
                  "transition-colors",
                  clickable && "cursor-pointer hover:bg-surface-muted focus:bg-surface-muted",
                )}
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={cn(
                      "px-4 py-2.5 align-middle",
                      c.align === "right" ? "text-right" : "text-left",
                      hideClass(c.hideBelow),
                    )}
                  >
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Charts — plain SVG, no dependency                                           */
/* -------------------------------------------------------------------------- */
export function ChartCard({
  title,
  description,
  scope,
  children,
  action,
  className,
}: {
  title: string;
  description?: string;
  scope?: string;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn("flex flex-col", className)}>
      <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-fg">{title}</h3>
          {description && <p className="mt-0.5 text-2xs text-fg-muted">{description}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {scope && <span className="text-2xs text-fg-faint">{scope}</span>}
          {action}
        </div>
      </div>
      <div className="min-h-0 flex-1 p-4">{children}</div>
    </Card>
  );
}

/**
 * Horizontal bar comparison — the clearest way to read "baseline vs variant"
 * for a handful of metrics, and far more legible than a grouped column chart
 * at this data volume.
 */
export function ComparisonBars({
  items,
  leftLabel,
  rightLabel,
}: {
  items: { label: string; left: number; right: number; format: (v: number) => string }[];
  leftLabel: string;
  rightLabel: string;
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 text-2xs">
        <span className="flex items-center gap-1.5 text-fg-muted">
          <span className="h-2 w-2 rounded-sm bg-fg-faint" aria-hidden />
          {leftLabel}
        </span>
        <span className="flex items-center gap-1.5 text-fg-muted">
          <span className="h-2 w-2 rounded-sm bg-brand" aria-hidden />
          {rightLabel}
        </span>
      </div>

      {items.map((item) => {
        const max = Math.max(item.left, item.right, 1);
        return (
          <div key={item.label}>
            <div className="mb-1.5 flex items-baseline justify-between gap-3">
              <span className="text-xs text-fg-muted">{item.label}</span>
              <span className="tnum text-xs font-medium text-fg">{item.format(item.right)}</span>
            </div>
            <div className="space-y-1">
              <Bar value={item.left} max={max} tone="neutral" title={`${leftLabel}: ${item.format(item.left)}`} />
              <Bar value={item.right} max={max} tone="brand" title={`${rightLabel}: ${item.format(item.right)}`} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Bar({
  value,
  max,
  tone,
  title,
}: {
  value: number;
  max: number;
  tone: "neutral" | "brand";
  title: string;
}) {
  const pct = max > 0 ? Math.max((value / max) * 100, value > 0 ? 1.5 : 0) : 0;
  return (
    <div
      className="h-2 w-full overflow-hidden rounded-sm bg-surface-muted"
      role="img"
      aria-label={title}
      title={title}
    >
      <div
        className={cn("h-full rounded-sm transition-all", tone === "brand" ? "bg-brand" : "bg-fg-faint")}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

/** Compact distribution bar — used for order-status mix. */
export function StackedBar({
  segments,
  total,
}: {
  segments: { label: string; value: number; className: string }[];
  total: number;
}) {
  if (total <= 0) {
    return <div className="h-2 w-full rounded-sm bg-surface-muted" aria-hidden />;
  }
  return (
    <>
      <div className="flex h-2 w-full overflow-hidden rounded-sm bg-surface-muted">
        {segments
          .filter((s) => s.value > 0)
          .map((s) => (
            <div
              key={s.label}
              className={s.className}
              style={{ width: `${(s.value / total) * 100}%` }}
              title={`${s.label}: ${s.value}`}
            />
          ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
        {segments.map((s) => (
          <span key={s.label} className="flex items-center gap-1.5 text-2xs text-fg-muted">
            <span className={cn("h-2 w-2 rounded-sm", s.className)} aria-hidden />
            {s.label}
            <span className="tnum font-medium text-fg">{s.value}</span>
          </span>
        ))}
      </div>
    </>
  );
}

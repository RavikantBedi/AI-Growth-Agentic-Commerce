import {
  Bot,
  ChevronsLeft,
  ChevronsRight,
  Cpu,
  CreditCard,
  ExternalLink,
  FileSearch,
  LayoutDashboard,
  Menu,
  Moon,
  Package,
  Receipt,
  ShieldCheck,
  Sun,
  TrendingUp,
  WifiOff,
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { EnvironmentBadge } from "@/ui/feedback";
import { Badge, IconButton } from "@/ui/primitives";
import type { Health } from "@/lib/api";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

export type RouteId =
  | "overview"
  | "catalog"
  | "agent"
  | "growth"
  | "orders"
  | "audit"
  | "buyer";

export const ROUTES: {
  id: RouteId;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  group: "console" | "storefront";
  title: string;
  description: string;
}[] = [
  {
    id: "overview",
    label: "Overview",
    icon: LayoutDashboard,
    group: "console",
    title: "Overview",
    description: "Commerce performance, AI-assisted growth and payment health.",
  },
  {
    id: "catalog",
    label: "Catalog",
    icon: Package,
    group: "console",
    title: "Catalog",
    description: "The single source of truth for every price the system charges.",
  },
  {
    id: "agent",
    label: "AI Agent",
    icon: Bot,
    group: "console",
    title: "AI Agent",
    description: "Control what your agent can see, recommend and do.",
  },
  {
    id: "growth",
    label: "Growth",
    icon: TrendingUp,
    group: "console",
    title: "Growth",
    description: "Measure the revenue impact of AI-assisted commerce.",
  },
  {
    id: "orders",
    label: "Orders",
    icon: Receipt,
    group: "console",
    title: "Orders",
    description: "Every order, its payment state and its verification result.",
  },
  {
    id: "audit",
    label: "Audit",
    icon: FileSearch,
    group: "console",
    title: "Audit explorer",
    description: "Every money-relevant decision, with its reason and outcome.",
  },
  {
    id: "buyer",
    label: "AI Buyer",
    icon: Bot,
    group: "storefront",
    title: "AI Buyer",
    description: "The shopper-facing agentic commerce experience.",
  },
];

const SIDEBAR_KEY = "amg.sidebar.collapsed";

export function AppShell({
  route,
  onNavigate,
  health,
  offline,
  children,
}: {
  route: RouteId;
  onNavigate: (r: RouteId) => void;
  health: Health | null;
  offline: boolean;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [mobileOpen, setMobileOpen] = useState(false);
  const { theme, toggle } = useTheme();

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [collapsed]);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => setMobileOpen(false), [route]);

  const current = ROUTES.find((r) => r.id === route) ?? ROUTES[0];
  const simulated = health?.payment_provider.simulated ?? false;

  return (
    <div className="flex h-full bg-bg">
      {/* Skip link — the first thing a keyboard user reaches. */}
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-[80] focus:rounded-md focus:bg-accent focus:px-3 focus:py-2 focus:text-sm focus:text-accent-fg"
      >
        Skip to content
      </a>

      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-fg/40 lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden
        />
      )}

      <Sidebar
        route={route}
        onNavigate={onNavigate}
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed((c) => !c)}
        mobileOpen={mobileOpen}
        onCloseMobile={() => setMobileOpen(false)}
        health={health}
        offline={offline}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex shrink-0 items-center justify-between gap-3 border-b border-line bg-surface/90 px-4 py-2.5 backdrop-blur">
          <div className="flex min-w-0 items-center gap-2.5">
            <IconButton
              icon={Menu}
              label="Open navigation"
              onClick={() => setMobileOpen(true)}
              className="lg:hidden"
            />
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-fg">{current.title}</h2>
              <p className="hidden truncate text-2xs text-fg-muted sm:block">
                {current.description}
              </p>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1.5">
            {offline ? (
              <Badge tone="danger" icon={WifiOff} dot>
                <span className="hidden sm:inline">Backend unreachable</span>
                <span className="sm:hidden">Offline</span>
              </Badge>
            ) : (
              health && (
                <>
                  <Badge
                    tone={health.llm.deterministic ? "warning" : "success"}
                    icon={Cpu}
                    className="hidden md:inline-flex"
                  >
                    {health.llm.deterministic ? "Deterministic" : health.llm.provider}
                  </Badge>
                  <EnvironmentBadge
                    provider={health.payment_provider.name}
                    simulated={simulated}
                  />
                </>
              )
            )}
            <IconButton
              icon={theme === "dark" ? Sun : Moon}
              label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              onClick={toggle}
            />
            <a
              href="/docs"
              target="_blank"
              rel="noreferrer"
              className="hidden h-8 items-center gap-1 rounded-md border border-line px-2 text-2xs text-fg-muted transition-colors hover:text-fg sm:inline-flex"
            >
              API
              <ExternalLink className="h-3 w-3" aria-hidden />
            </a>
          </div>
        </header>

        <main id="main" className="min-h-0 flex-1 overflow-y-auto">
          {children}
        </main>
      </div>
    </div>
  );
}

function Sidebar({
  route,
  onNavigate,
  collapsed,
  onToggleCollapse,
  mobileOpen,
  onCloseMobile,
  health,
  offline,
}: {
  route: RouteId;
  onNavigate: (r: RouteId) => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
  health: Health | null;
  offline: boolean;
}) {
  const consoleRoutes = ROUTES.filter((r) => r.group === "console");
  const storefrontRoutes = ROUTES.filter((r) => r.group === "storefront");

  return (
    <nav
      aria-label="Main navigation"
      className={cn(
        "fixed inset-y-0 left-0 z-50 flex shrink-0 flex-col border-r border-line bg-surface",
        "transition-[width] duration-200 lg:static",
        // `hidden` rather than a translate: a closed drawer parked off-screen
        // stays focusable and screen-reader visible, so Tab lands on links the
        // user cannot see.
        mobileOpen ? "flex animate-slide-in-left" : "hidden lg:flex",
        collapsed ? "w-[248px] lg:w-[72px]" : "w-[248px]",
      )}
    >
      <div className="flex h-[53px] shrink-0 items-center gap-2.5 border-b border-line px-3">
        <div className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-accent text-accent-fg">
          <ShieldCheck className="h-4 w-4" aria-hidden />
        </div>
        {!collapsed && (
          <div className="min-w-0 flex-1 leading-tight">
            <p className="truncate text-xs font-semibold text-fg">AI Merchant Growth</p>
            <p className="truncate text-2xs text-fg-faint">Agentic Checkout</p>
          </div>
        )}
        <IconButton
          icon={X}
          label="Close navigation"
          onClick={onCloseMobile}
          size="sm"
          className="lg:hidden"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
        <NavGroup label="Console" collapsed={collapsed}>
          {consoleRoutes.map((r) => (
            <NavItem
              key={r.id}
              route={r}
              active={route === r.id}
              collapsed={collapsed}
              onClick={() => onNavigate(r.id)}
            />
          ))}
        </NavGroup>

        <NavGroup label="Storefront" collapsed={collapsed} className="mt-4">
          {storefrontRoutes.map((r) => (
            <NavItem
              key={r.id}
              route={r}
              active={route === r.id}
              collapsed={collapsed}
              onClick={() => onNavigate(r.id)}
            />
          ))}
        </NavGroup>
      </div>

      {/* System status — always answers "what am I connected to?" */}
      <div className="shrink-0 border-t border-line p-2">
        {!collapsed && (
          <div className="mb-2 space-y-1.5 rounded-md bg-surface-muted p-2.5">
            <p className="text-2xs font-medium uppercase tracking-wide text-fg-faint">
              System status
            </p>
            <StatusLine
              icon={Cpu}
              label="AI provider"
              value={
                offline
                  ? "Unknown"
                  : health
                    ? health.llm.deterministic
                      ? "Deterministic"
                      : `${health.llm.provider}`
                    : "…"
              }
              tone={offline ? "danger" : health?.llm.deterministic ? "warning" : "success"}
            />
            <StatusLine
              icon={CreditCard}
              label="Payments"
              value={
                offline
                  ? "Unknown"
                  : health
                    ? health.payment_provider.simulated
                      ? "Local sandbox"
                      : "Razorpay test"
                    : "…"
              }
              tone={offline ? "danger" : health?.payment_provider.simulated ? "warning" : "success"}
            />
          </div>
        )}

        <button
          onClick={onToggleCollapse}
          className="hidden w-full items-center gap-2 rounded-md px-2 py-1.5 text-2xs text-fg-muted transition-colors hover:bg-surface-muted hover:text-fg lg:flex"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <ChevronsRight className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <>
              <ChevronsLeft className="h-3.5 w-3.5" aria-hidden />
              Collapse
            </>
          )}
        </button>
      </div>
    </nav>
  );
}

function NavGroup({
  label,
  collapsed,
  children,
  className,
}: {
  label: string;
  collapsed: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      {!collapsed && (
        <p className="mb-1 px-2 text-2xs font-medium uppercase tracking-wide text-fg-faint">
          {label}
        </p>
      )}
      <ul className="space-y-0.5">{children}</ul>
    </div>
  );
}

function NavItem({
  route,
  active,
  collapsed,
  onClick,
}: {
  route: (typeof ROUTES)[number];
  active: boolean;
  collapsed: boolean;
  onClick: () => void;
}) {
  const Icon = route.icon;
  return (
    <li>
      <button
        onClick={onClick}
        aria-current={active ? "page" : undefined}
        title={collapsed ? route.label : undefined}
        className={cn(
          "flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-xs font-medium transition-colors",
          collapsed && "lg:justify-center",
          active
            ? "bg-surface-muted text-fg"
            : "text-fg-muted hover:bg-surface-muted hover:text-fg",
        )}
      >
        <Icon
          className={cn("h-4 w-4 shrink-0", active ? "text-brand" : "text-fg-faint")}
          aria-hidden
        />
        {!collapsed && <span className="truncate">{route.label}</span>}
        {collapsed && <span className="sr-only lg:not-sr-only lg:hidden">{route.label}</span>}
      </button>
    </li>
  );
}

function StatusLine({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
  tone: "success" | "warning" | "danger";
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="flex items-center gap-1.5 text-2xs text-fg-muted">
        <Icon className="h-3 w-3" aria-hidden />
        {label}
      </span>
      <span className="flex items-center gap-1 text-2xs font-medium text-fg">
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            tone === "success" && "bg-success",
            tone === "warning" && "bg-warning",
            tone === "danger" && "bg-danger",
          )}
          aria-hidden
        />
        <span className="truncate">{value}</span>
      </span>
    </div>
  );
}

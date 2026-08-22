import { useCallback, useEffect, useState } from "react";

import { AppShell, ROUTES, type RouteId } from "@/components/shell/AppShell";
import { BuyerInterface } from "@/components/buyer/BuyerInterface";
import { AgentPage } from "@/components/merchant/AgentPage";
import { AuditPage } from "@/components/merchant/AuditPage";
import { CatalogPage } from "@/components/merchant/CatalogPage";
import { GrowthPage } from "@/components/merchant/GrowthPage";
import { OrdersPage } from "@/components/merchant/OrdersPage";
import { OverviewPage } from "@/components/merchant/OverviewPage";
import { ToastProvider } from "@/ui/feedback";
import { api, type Health } from "@/lib/api";

const VALID = new Set(ROUTES.map((r) => r.id));

function routeFromHash(): RouteId {
  const raw = window.location.hash.replace(/^#\/?/, "").split("?")[0];
  return VALID.has(raw as RouteId) ? (raw as RouteId) : "overview";
}

export default function App() {
  return (
    <ToastProvider>
      <Shell />
    </ToastProvider>
  );
}

function Shell() {
  const [route, setRoute] = useState<RouteId>(routeFromHash);
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);
  /** Set when a page wants the audit explorer opened on a specific order.
   *  Seeded from `?order=` so an investigation can be shared as a link. */
  const [auditOrderId, setAuditOrderId] = useState<string | null>(
    () => new URLSearchParams(window.location.search).get("order"),
  );

  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const navigate = useCallback((next: RouteId) => {
    setRoute(next);
    window.location.hash = `#/${next}`;
  }, []);

  const inspectOrder = useCallback(
    (orderId: string) => {
      setAuditOrderId(orderId);
      navigate("audit");
    },
    [navigate],
  );

  useEffect(() => {
    let alive = true;
    const poll = () =>
      api
        .health()
        .then((h) => {
          if (!alive) return;
          setHealth(h);
          setOffline(false);
        })
        .catch(() => alive && setOffline(true));
    void poll();
    const timer = setInterval(poll, 20000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <AppShell route={route} onNavigate={navigate} health={health} offline={offline}>
      {route === "overview" && <OverviewPage onInspectOrder={inspectOrder} onNavigate={navigate} />}
      {route === "catalog" && <CatalogPage />}
      {route === "agent" && <AgentPage health={health} />}
      {route === "growth" && <GrowthPage />}
      {route === "orders" && <OrdersPage onInspectOrder={inspectOrder} />}
      {route === "audit" && (
        <AuditPage focusOrderId={auditOrderId} onClearFocus={() => setAuditOrderId(null)} />
      )}
      {route === "buyer" && <BuyerInterface health={health} onInspectOrder={inspectOrder} />}
    </AppShell>
  );
}

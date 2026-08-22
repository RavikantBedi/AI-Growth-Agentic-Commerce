import {
  Bot,
  CheckCircle2,
  FileSearch,
  Filter,
  Receipt,
  ShieldCheck,
  User,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { DataTable, PageHeader, SearchInput, SectionTitle, type Column } from "@/ui/data";
import {
  Callout,
  EmptyState,
  ErrorState,
  StatusBadge,
  SyntheticBadge,
} from "@/ui/feedback";
import { Drawer } from "@/ui/overlays";
import { Badge, Button, Card, Field, KeyValue, Select, Skeleton } from "@/ui/primitives";
import { ApiError, api, type AuditEvent, type AuditStory } from "@/lib/api";
import { cn, formatTime, relativeTime, shortId } from "@/lib/utils";

/** Steps of the money gate, in the order they must occur. */
const TIMELINE_STEPS: { actions: string[]; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { actions: ["SESSION_STARTED"], label: "Session started", icon: User },
  { actions: ["PRODUCT_SEARCHED"], label: "Buyer request", icon: FileSearch },
  { actions: ["PRODUCT_RECOMMENDED"], label: "Product recommended", icon: Bot },
  { actions: ["UPSELL_SUGGESTED"], label: "Upsell suggested", icon: Bot },
  { actions: ["UPSELL_ACCEPTED", "CROSS_SELL_ACCEPTED"], label: "Add-on accepted", icon: CheckCircle2 },
  { actions: ["CART_UPDATED"], label: "Cart updated", icon: Receipt },
  { actions: ["PRICE_CALCULATED"], label: "Cart calculated", icon: Receipt },
  { actions: ["INVENTORY_CHECKED"], label: "Inventory checked", icon: ShieldCheck },
  { actions: ["POLICY_CHECKED"], label: "Policy check", icon: ShieldCheck },
  { actions: ["PAYMENT_CONFIRMATION_REQUESTED"], label: "Approval requested", icon: User },
  { actions: ["PAYMENT_CONFIRMED_BY_USER"], label: "User approved", icon: CheckCircle2 },
  { actions: ["PAYMENT_ORDER_CREATED"], label: "Payment order created", icon: Receipt },
  { actions: ["PAYMENT_ATTEMPTED"], label: "Payment attempted", icon: Receipt },
  { actions: ["PAYMENT_VERIFIED"], label: "Payment verified", icon: ShieldCheck },
  { actions: ["PAYMENT_FAILED", "PAYMENT_VERIFICATION_FAILED"], label: "Payment failed", icon: XCircle },
  { actions: ["INVENTORY_DECREMENTED"], label: "Stock reduced", icon: Receipt },
  { actions: ["ORDER_PAID"], label: "Order paid", icon: CheckCircle2 },
];

export function AuditPage({
  focusOrderId,
  onClearFocus,
}: {
  focusOrderId: string | null;
  onClearFocus: () => void;
}) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [actions, setActions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState({
    action: "",
    decision: "",
    session_id: "",
    order_id: "",
    include_synthetic: true,
  });
  const [storyOrderId, setStoryOrderId] = useState<string | null>(null);
  const [eventDetail, setEventDetail] = useState<AuditEvent | null>(null);

  useEffect(() => {
    if (focusOrderId) setStoryOrderId(focusOrderId);
  }, [focusOrderId]);

  const load = useCallback(async () => {
    try {
      const data = await api.audit.events({ ...filters, limit: 200 });
      setEvents(data.events);
      setError(null);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    void load();
    const timer = setInterval(load, 12000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    void api.audit
      .actions()
      .then((d) => setActions(d.actions))
      .catch(() => {});
  }, []);

  const hasFilters =
    filters.action || filters.decision || filters.session_id || filters.order_id;

  const columns: Column<AuditEvent>[] = [
    {
      key: "time",
      header: "Time",
      width: "6rem",
      render: (e) => (
        <span className="tnum whitespace-nowrap text-fg-faint" title={e.created_at}>
          {formatTime(e.created_at)}
        </span>
      ),
    },
    {
      key: "action",
      header: "Action",
      render: (e) => (
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-2xs text-fg">{e.action}</span>
          {e.is_synthetic && <SyntheticBadge />}
        </div>
      ),
    },
    {
      key: "actor",
      header: "Actor",
      hideBelow: "md",
      render: (e) => (
        <span className="text-fg-muted">
          {e.actor}
          <span className="ml-1 text-2xs text-fg-faint">({e.actor_type})</span>
        </span>
      ),
    },
    {
      key: "reason",
      header: "Reason",
      hideBelow: "lg",
      render: (e) => (
        <span className="line-clamp-2 max-w-lg leading-relaxed text-fg-muted">{e.reason}</span>
      ),
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      render: (e) => (
        <span className="tnum whitespace-nowrap text-fg">{e.amount_display ?? "—"}</span>
      ),
    },
    {
      key: "decision",
      header: "Decision",
      render: (e) => (e.decision ? <StatusBadge status={e.decision} /> : null),
    },
    {
      key: "order",
      header: "Order",
      align: "right",
      hideBelow: "sm",
      render: (e) => (
        <span className="font-mono text-2xs text-fg-faint">{shortId(e.order_id, 5)}</span>
      ),
    },
  ];

  if (error && events.length === 0) {
    return (
      <div className="p-4 sm:p-6">
        <ErrorState
          title="Unable to load audit events"
          message={`The audit service did not respond. ${error}`}
          onRetry={() => void load()}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4 sm:p-6">
      <PageHeader
        title="Audit explorer"
        description="Every money-relevant decision, with its reason, policy verdict and outcome. Select any row to open the full investigation, or any order to replay its money-action story."
      />

      <Card className="p-3">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
          <Field label="Action" htmlFor="f-action">
            <Select
              id="f-action"
              value={filters.action}
              onChange={(e) => setFilters({ ...filters, action: e.target.value })}
            >
              <option value="">All actions</option>
              {actions.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Decision" htmlFor="f-decision">
            <Select
              id="f-decision"
              value={filters.decision}
              onChange={(e) => setFilters({ ...filters, decision: e.target.value })}
            >
              <option value="">Any decision</option>
              <option value="ALLOWED">Allowed</option>
              <option value="REJECTED">Rejected</option>
              <option value="INFO">Info</option>
            </Select>
          </Field>
          <Field label="Order id" htmlFor="f-order">
            <SearchInput
              label="Filter by order id"
              value={filters.order_id}
              onChange={(v) => setFilters({ ...filters, order_id: v })}
              placeholder="ord_…"
            />
          </Field>
          <Field label="Session id" htmlFor="f-session">
            <SearchInput
              label="Filter by session id"
              value={filters.session_id}
              onChange={(v) => setFilters({ ...filters, session_id: v })}
              placeholder="ses_…"
            />
          </Field>
          <div className="flex items-end gap-2">
            <label className="flex h-9 cursor-pointer items-center gap-2 text-xs text-fg-muted">
              <input
                type="checkbox"
                checked={filters.include_synthetic}
                onChange={(e) =>
                  setFilters({ ...filters, include_synthetic: e.target.checked })
                }
                className="h-3.5 w-3.5 accent-brand"
              />
              Synthetic
            </label>
            {hasFilters && (
              <Button
                size="sm"
                icon={Filter}
                onClick={() =>
                  setFilters({
                    action: "",
                    decision: "",
                    session_id: "",
                    order_id: "",
                    include_synthetic: true,
                  })
                }
              >
                Reset
              </Button>
            )}
          </div>
        </div>
      </Card>

      <Card className="overflow-hidden">
        <DataTable
          caption="Audit events"
          columns={columns}
          rows={events}
          loading={loading}
          getRowKey={(e) => String(e.id)}
          onRowClick={setEventDetail}
          emptyState={
            <EmptyState
              icon={FileSearch}
              title={hasFilters ? "No events match these filters" : "No audit activity yet"}
              description={
                hasFilters
                  ? "Try widening the filters."
                  : "Audit activity appears here after commerce actions — searches, cart changes, policy checks, approvals and payments."
              }
            />
          }
        />
      </Card>

      <EventDrawer
        event={eventDetail}
        onClose={() => setEventDetail(null)}
        onOpenStory={(id) => {
          setEventDetail(null);
          setStoryOrderId(id);
        }}
      />

      <StoryDrawer
        orderId={storyOrderId}
        onClose={() => {
          setStoryOrderId(null);
          onClearFocus();
        }}
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Single-event investigation                                                  */
/* -------------------------------------------------------------------------- */
function EventDrawer({
  event,
  onClose,
  onOpenStory,
}: {
  event: AuditEvent | null;
  onClose: () => void;
  onOpenStory: (orderId: string) => void;
}) {
  if (!event) return null;
  const policy = event.policy_result as
    | { allowed?: boolean; checks?: { rule: string; passed: boolean; message: string }[] }
    | undefined;

  return (
    <Drawer
      open
      onClose={onClose}
      title={event.action}
      description={`${event.actor} · ${relativeTime(event.created_at)}`}
      footer={
        event.order_id && (
          <div className="flex justify-end">
            <Button variant="primary" icon={Receipt} onClick={() => onOpenStory(event.order_id!)}>
              View full money-action story
            </Button>
          </div>
        )
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          {event.decision && <StatusBadge status={event.decision} />}
          {event.status && <StatusBadge status={event.status} />}
          {event.is_synthetic && <SyntheticBadge />}
        </div>

        <Callout tone={event.decision === "REJECTED" ? "danger" : "info"} title="What happened">
          {event.reason}
        </Callout>

        <dl className="rounded-md border border-line px-3 py-1">
          <KeyValue label="Timestamp" mono>{event.created_at}</KeyValue>
          <KeyValue label="Actor">{`${event.actor} (${event.actor_type})`}</KeyValue>
          <KeyValue label="Amount" mono>{event.amount_display ?? "—"}</KeyValue>
          <KeyValue label="Order" mono>{event.order_id ?? "—"}</KeyValue>
          <KeyValue label="Session" mono>{event.session_id ?? "—"}</KeyValue>
          <KeyValue label="Request id" mono>{event.request_id ?? "—"}</KeyValue>
          <KeyValue label="Payment reference" mono>{event.payment_reference ?? "—"}</KeyValue>
        </dl>

        {policy?.checks && policy.checks.length > 0 && (
          <section>
            <h4 className="mb-1.5 text-xs font-semibold text-fg">Policy result</h4>
            <PolicyChecks checks={policy.checks} />
          </section>
        )}

        {Object.keys(event.input ?? {}).length > 0 && (
          <section>
            <h4 className="mb-1.5 text-xs font-semibold text-fg">Input</h4>
            <pre className="max-h-64 overflow-auto rounded-md border border-line bg-surface-muted p-3 text-2xs leading-relaxed text-fg-muted">
              {JSON.stringify(event.input, null, 2)}
            </pre>
          </section>
        )}
      </div>
    </Drawer>
  );
}

export function PolicyChecks({
  checks,
}: {
  checks: { rule: string; passed: boolean; message: string }[];
}) {
  return (
    <ul className="space-y-1 rounded-md border border-line p-2.5">
      {checks.map((c) => (
        <li key={c.rule} className="flex items-start gap-2">
          {c.passed ? (
            <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-success" aria-hidden />
          ) : (
            <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-danger" aria-hidden />
          )}
          <div className="min-w-0">
            <p className="font-mono text-2xs uppercase tracking-wide text-fg-faint">{c.rule}</p>
            <p className={cn("text-2xs leading-relaxed", c.passed ? "text-fg-muted" : "text-danger")}>
              {c.message}
            </p>
          </div>
          <span className="sr-only">{c.passed ? "Passed" : "Failed"}</span>
        </li>
      ))}
    </ul>
  );
}

/* -------------------------------------------------------------------------- */
/* Money-action story                                                          */
/* -------------------------------------------------------------------------- */
function StoryDrawer({ orderId, onClose }: { orderId: string | null; onClose: () => void }) {
  const [story, setStory] = useState<AuditStory | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!orderId) {
      setStory(null);
      return;
    }
    setLoading(true);
    api.audit
      .story(orderId)
      .then((s) => {
        setStory(s);
        setError(null);
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }, [orderId]);

  if (!orderId) return null;

  const n = story?.narrative;
  const verified = n?.was_it_verified;

  return (
    <Drawer
      open
      onClose={onClose}
      width="lg"
      title="Money action story"
      description={story ? `Order ${shortId(story.order_id, 8)}` : undefined}
    >
      {loading ? (
        <div className="space-y-3">
          <Skeleton className="h-16" />
          <Skeleton className="h-40" />
          <Skeleton className="h-64" />
        </div>
      ) : error || !story || !n ? (
        <ErrorState title="Could not load this story" message={error ?? undefined} />
      ) : (
        <div className="space-y-5">
          {/* Verdict first — the question a reviewer actually has. */}
          <div
            className={cn(
              "rounded-md border p-3",
              verified?.order_is_paid
                ? "border-success/25 bg-success-soft"
                : "border-warning/25 bg-warning-soft",
            )}
          >
            <div className="flex items-start gap-2.5">
              {verified?.order_is_paid ? (
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
              ) : (
                <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
              )}
              <div className="min-w-0">
                <p className="text-xs font-semibold text-fg">Was it verified?</p>
                <p className="mt-0.5 text-xs leading-relaxed text-fg-muted">
                  {verified?.statement}
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <StatusBadge status={story.status} />
                  <StatusBadge status={verified?.payment_status} />
                  <StatusBadge status={verified?.verification_status} />
                  {story.is_synthetic && <SyntheticBadge />}
                </div>
              </div>
            </div>
          </div>

          {/* The narrative answers */}
          <div className="grid gap-2 sm:grid-cols-2">
            <Answer question="What was requested" answer={n.what_was_requested} />
            <Answer question="Why the agent selected it" answer={n.what_the_agent_selected} />
            <Answer question="What was suggested" answer={n.what_was_suggested} />
            <Answer
              question="Who approved"
              answer={
                n.who_approved.actor
                  ? `${n.who_approved.actor} (${n.who_approved.actor_type}) — ${n.who_approved.decision}. ${n.who_approved.detail}`
                  : n.who_approved.detail
              }
            />
          </div>

          <section>
            <SectionTitle title="What is being bought" />
            <div className="overflow-hidden rounded-md border border-line">
              <table className="w-full text-xs">
                <tbody className="divide-y divide-line">
                  {n.what_is_being_bought.map((item) => (
                    <tr key={item.sku}>
                      <td className="px-3 py-2">
                        <span className="text-fg">{item.name}</span>
                        {item.how_it_entered_the_cart !== "direct" && (
                          <Badge tone="brand" className="ml-1.5">
                            {item.how_it_entered_the_cart.replace("_", "-")}
                          </Badge>
                        )}
                      </td>
                      <td className="tnum px-3 py-2 text-right text-fg-muted">
                        {item.quantity} × {item.unit_price_display}
                      </td>
                      <td className="tnum px-3 py-2 text-right font-medium text-fg">
                        {item.line_total_display}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <dl className="mt-2 rounded-md border border-line px-3 py-1">
              {Object.entries(n.how_much).map(([k, v]) => (
                <KeyValue key={k} label={k.replace(/_/g, " ")} mono>
                  {v}
                </KeyValue>
              ))}
            </dl>
          </section>

          {"checks" in n.which_policy && n.which_policy.checks && (
            <section>
              <SectionTitle title="Which policy" description={n.policy_verdict} />
              <PolicyChecks checks={n.which_policy.checks} />
            </section>
          )}

          <section>
            <SectionTitle title="Provider and result" />
            <dl className="rounded-md border border-line px-3 py-1">
              <KeyValue label="Provider" mono>{n.which_provider.provider ?? "—"}</KeyValue>
              <KeyValue label="Provider order" mono>
                {n.which_provider.provider_order_id ?? "—"}
              </KeyValue>
              <KeyValue label="Test mode" mono>{String(n.which_provider.test_mode)}</KeyValue>
              <KeyValue label="Signature checked" mono>
                {String(verified?.signature_checked)}
              </KeyValue>
            </dl>
            <p className="mt-1.5 text-xs leading-relaxed text-fg-muted">
              {n.what_was_the_result}
            </p>
          </section>

          {story.approved_quote && (
            <section>
              <SectionTitle
                title="The exact text the buyer approved"
                description={`Confirmed by ${story.approved_quote.confirmed_by ?? "—"}`}
              />
              <pre className="overflow-x-auto rounded-md border border-line bg-surface-muted p-3 text-2xs leading-relaxed text-fg-muted">
                {story.approved_quote.explanation}
              </pre>
              <p className="mt-1 font-mono text-2xs text-fg-faint">
                fingerprint {story.approved_quote.cart_fingerprint.slice(0, 32)}…
              </p>
            </section>
          )}

          <section>
            <SectionTitle
              title="Timeline"
              description={`${story.event_count} events, oldest first`}
            />
            <Timeline events={story.timeline} />
          </section>
        </div>
      )}
    </Drawer>
  );
}

function Answer({ question, answer }: { question: string; answer: string }) {
  return (
    <div className="rounded-md border border-line p-3">
      <p className="text-2xs font-medium uppercase tracking-wide text-fg-faint">{question}</p>
      <p className="mt-1 text-xs leading-relaxed text-fg-muted">{answer}</p>
    </div>
  );
}

function Timeline({ events }: { events: AuditEvent[] }) {
  return (
    <ol className="relative space-y-0">
      {events.map((e, i) => {
        const step = TIMELINE_STEPS.find((s) => s.actions.includes(e.action));
        const Icon = step?.icon ?? FileSearch;
        const rejected = e.decision === "REJECTED";
        const isLast = i === events.length - 1;
        return (
          <li key={e.id} className="relative flex gap-3 pb-3">
            {!isLast && (
              <span
                className="absolute left-[11px] top-6 h-full w-px bg-line"
                aria-hidden
              />
            )}
            <span
              className={cn(
                "relative z-10 mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border",
                rejected
                  ? "border-danger/30 bg-danger-soft text-danger"
                  : e.action === "ORDER_PAID" || e.action === "PAYMENT_VERIFIED"
                    ? "border-success/30 bg-success-soft text-success"
                    : "border-line bg-surface text-fg-faint",
              )}
            >
              <Icon className="h-3 w-3" aria-hidden />
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="text-xs font-medium text-fg">
                  {step?.label ?? e.action.replace(/_/g, " ").toLowerCase()}
                </span>
                <span className="tnum text-2xs text-fg-faint">{formatTime(e.created_at)}</span>
                {e.amount_display && (
                  <span className="tnum text-2xs font-medium text-fg-muted">
                    {e.amount_display}
                  </span>
                )}
                {e.decision && e.decision !== "INFO" && <StatusBadge status={e.decision} />}
              </div>
              <p className="mt-0.5 text-2xs leading-relaxed text-fg-muted">{e.reason}</p>
              <p className="mt-0.5 font-mono text-2xs text-fg-faint">
                {e.action} · {e.actor}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

import {
  FlaskConical,
  Megaphone,
  Play,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ChartCard, ComparisonBars, MetricCard, PageHeader, SectionTitle } from "@/ui/data";
import { Callout, EmptyState, StatusBadge, SyntheticBadge, useToast } from "@/ui/feedback";
import { Button, Card, CardHeader, Field, Input, Skeleton } from "@/ui/primitives";
import {
  ApiError,
  api,
  type Campaign,
  type ComparisonDelta,
  type Metrics,
  type SimulationResult,
} from "@/lib/api";
import { cn, formatINR, relativeTime } from "@/lib/utils";

/** Stages shown while the simulation request is genuinely in flight. */
const STAGES = [
  "Preparing experiment",
  "Running baseline sessions",
  "Running AI-assisted sessions",
  "Verifying payments",
  "Comparing results",
];

export function GrowthPage() {
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [running, setRunning] = useState(false);
  const [stage, setStage] = useState(0);
  const [sessions, setSessions] = useState(40);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [proposing, setProposing] = useState(false);
  const [loading, setLoading] = useState(true);
  const toast = useToast();

  const loadCampaigns = useCallback(async () => {
    try {
      setCampaigns((await api.merchant.campaigns()).campaigns);
    } catch {
      /* non-fatal: the experiment section still works */
    }
  }, []);

  useEffect(() => {
    void loadCampaigns();
    void api.merchant
      .experiments()
      .then((d) => d.experiments[0] && setResult(d.experiments[0].results))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [loadCampaigns]);

  async function runSimulation() {
    setRunning(true);
    setStage(0);
    // Advance through stages while the real request runs; the request itself
    // decides when we finish, so progress never outlives the work.
    const ticker = setInterval(
      () => setStage((s) => Math.min(s + 1, STAGES.length - 2)),
      Math.max(400, sessions * 12),
    );
    try {
      const data = await api.merchant.simulate(sessions);
      setStage(STAGES.length - 1);
      setResult(data);
      toast.push(
        "success",
        "Simulation complete",
        `${sessions} sessions per arm in ${data.duration_seconds}s.`,
      );
    } catch (e) {
      toast.push("danger", "Simulation failed", (e as ApiError).message);
    } finally {
      clearInterval(ticker);
      setRunning(false);
    }
  }

  async function proposeCampaign() {
    setProposing(true);
    try {
      const proposal = await api.merchant.recommendCampaign();
      toast.push(
        "info",
        "Campaign proposed — awaiting your approval",
        `Requested ${proposal.requested_discount_percent}%, clamped to ${proposal.applied_discount_percent}% by your discount cap.`,
      );
      void loadCampaigns();
    } catch (e) {
      toast.push("danger", "Could not propose a campaign", (e as ApiError).message);
    } finally {
      setProposing(false);
    }
  }

  async function decide(campaign: Campaign, approve: boolean) {
    try {
      if (approve) {
        await api.merchant.approveCampaign(campaign.id, "merchant-owner");
        toast.push("success", "Campaign activated", campaign.name);
      } else {
        await api.merchant.rejectCampaign(campaign.id, "merchant-owner", "Rejected by merchant.");
        toast.push("info", "Campaign rejected", campaign.name);
      }
      void loadCampaigns();
    } catch (e) {
      toast.push("danger", "Could not update the campaign", (e as ApiError).message);
    }
  }

  const pending = campaigns.filter((c) => c.status === "PENDING_APPROVAL");
  const delta = (key: string) => result?.comparison[key] as ComparisonDelta | undefined;

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Growth"
        description="Measure the revenue impact of AI-assisted commerce. Both arms run through the same retrieval, pricing, policy and payment-verification code a live buyer uses."
        actions={
          <div className="flex items-end gap-2">
            <Field label="Sessions per arm" htmlFor="sessions" className="w-32">
              <Input
                id="sessions"
                type="number"
                min={1}
                max={500}
                value={sessions}
                onChange={(e) => setSessions(Number(e.target.value))}
                disabled={running}
              />
            </Field>
            <Button
              variant="primary"
              icon={Play}
              loading={running}
              onClick={() => void runSimulation()}
              className="mb-0.5"
            >
              Run growth simulation
            </Button>
          </div>
        }
      />

      {running && <SimulationProgress stage={stage} />}

      {loading && !result ? (
        <div className="grid gap-3 lg:grid-cols-2">
          <Skeleton className="h-52" />
          <Skeleton className="h-52" />
        </div>
      ) : !result ? (
        <Card>
          <EmptyState
            icon={FlaskConical}
            title="No experiment has been run yet"
            description="The simulator drives synthetic buyers through the real commerce pipeline. Nothing is measured until you run it — these numbers are computed, not stored."
            action={
              <Button variant="primary" icon={Play} loading={running} onClick={() => void runSimulation()}>
                Run growth simulation
              </Button>
            }
          />
        </Card>
      ) : (
        <>
          <Callout tone="warning" title={result.label_warning}>
            {String(result.assumptions.note)}
          </Callout>

          {/* Headline deltas */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <MetricCard
              label="Average order value"
              value={result.ai_assisted.aov_display}
              delta={delta("aov_paise")?.percent_change ?? null}
              deltaLabel="vs baseline"
              hint={`Baseline ${result.baseline.aov_display}`}
              emphasis="success"
            />
            <MetricCard
              label="Revenue / session"
              value={result.ai_assisted.revenue_per_session_display}
              delta={delta("revenue_per_session_paise")?.percent_change ?? null}
              deltaLabel="vs baseline"
              hint={`Baseline ${result.baseline.revenue_per_session_display}`}
            />
            <MetricCard
              label="Conversion"
              value={`${result.ai_assisted.conversion_rate_percent}%`}
              delta={delta("conversion_rate_percent")?.percent_change ?? null}
              deltaLabel="vs baseline"
              hint="Paired draws — identical by design"
            />
            <MetricCard
              label="Add-on acceptance"
              value={`${result.ai_assisted.addon_acceptance_rate_percent}%`}
              hint={`${result.ai_assisted.cross_sell_revenue_display} add-on revenue`}
              emphasis="success"
            />
          </div>

          <div className="grid gap-3 lg:grid-cols-5">
            <ChartCard
              title="Baseline vs AI-assisted"
              description="Same synthetic buyers, same payment outcomes — only add-ons differ"
              scope={`${result.sessions_per_arm} sessions/arm`}
              className="lg:col-span-3"
            >
              <ComparisonBars
                leftLabel="Baseline"
                rightLabel="AI-assisted"
                items={[
                  {
                    label: "Average order value",
                    left: result.baseline.aov_paise,
                    right: result.ai_assisted.aov_paise,
                    format: formatINR,
                  },
                  {
                    label: "Revenue per session",
                    left: result.baseline.revenue_per_session_paise,
                    right: result.ai_assisted.revenue_per_session_paise,
                    format: formatINR,
                  },
                  {
                    label: "Gross merchandise value",
                    left: result.baseline.gmv_paise,
                    right: result.ai_assisted.gmv_paise,
                    format: formatINR,
                  },
                  {
                    label: "Conversion rate",
                    left: result.baseline.conversion_rate_percent,
                    right: result.ai_assisted.conversion_rate_percent,
                    format: (v) => `${v.toFixed(2)}%`,
                  },
                ]}
              />
            </ChartCard>

            <div className="grid gap-3 lg:col-span-2">
              <ArmCard title="Baseline" subtitle="Upsell suppressed" metrics={result.baseline} />
              <ArmCard
                title="AI-assisted"
                subtitle="Bounded upsell enabled"
                metrics={result.ai_assisted}
                highlight
              />
            </div>
          </div>

          <Callout tone="info">
            {String(result.comparison.statistical_note ?? "")} {result.payment_note}
          </Callout>

          <p className="font-mono text-2xs text-fg-faint">
            experiment {result.experiment_id} · seed {result.seed} · {result.sessions_per_arm}{" "}
            sessions/arm · {result.duration_seconds}s
          </p>
        </>
      )}

      {/* ------------------------------------------------------- campaigns */}
      <div>
        <SectionTitle
          title="Campaigns"
          description="The AI may propose a campaign. Only you can activate one."
          action={
            <Button icon={FlaskConical} loading={proposing} onClick={() => void proposeCampaign()}>
              Ask the AI for a campaign
            </Button>
          }
        />

        {pending.length > 0 && (
          <Callout
            tone="warning"
            className="mb-3"
            title={`${pending.length} campaign${pending.length === 1 ? "" : "s"} awaiting your approval`}
          >
            An AI-proposed campaign is created as PENDING_APPROVAL and changes no price until
            you activate it.
          </Callout>
        )}

        {campaigns.length === 0 ? (
          <Card>
            <EmptyState
              icon={Megaphone}
              title="No campaigns yet"
              description="Ask the AI to propose one. It will be created for review, never activated automatically — and any discount above your cap is clamped and audited."
              action={
                <Button variant="primary" loading={proposing} onClick={() => void proposeCampaign()}>
                  Ask the AI for a campaign
                </Button>
              }
            />
          </Card>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {campaigns.map((c) => (
              <Card key={c.id} className="flex flex-col">
                <CardHeader
                  title={c.name}
                  description={`${c.created_by === "ai_agent" ? "Proposed by AI" : "Created by merchant"} · ${relativeTime(c.created_at)}`}
                  action={<StatusBadge status={c.status} />}
                />
                <div className="flex-1 space-y-3 px-4 pb-4">
                  {c.description && (
                    <p className="text-xs leading-relaxed text-fg-muted">{c.description}</p>
                  )}
                  <div className="grid grid-cols-3 gap-2">
                    <MiniFigure label="Discount" value={`${c.discount_percent}%`} />
                    <MiniFigure label="Budget" value={c.budget_display} />
                    <MiniFigure label="Spent" value={c.spent_display} />
                  </div>
                  {c.ai_rationale && (
                    <details className="group">
                      <summary className="cursor-pointer text-2xs text-fg-faint hover:text-fg-muted">
                        AI rationale
                      </summary>
                      <p className="mt-1 text-2xs leading-relaxed text-fg-muted">
                        {c.ai_rationale}
                      </p>
                    </details>
                  )}
                  {c.approved_by && (
                    <p className="text-2xs text-fg-faint">
                      {c.status === "REJECTED" ? "Rejected" : "Approved"} by {c.approved_by}
                    </p>
                  )}
                </div>
                {(c.status === "PENDING_APPROVAL" || c.status === "DRAFT") && (
                  <div className="flex gap-2 border-t border-line p-3">
                    <Button
                      variant="primary"
                      size="sm"
                      icon={ThumbsUp}
                      fullWidth
                      onClick={() => void decide(c, true)}
                    >
                      Approve &amp; activate
                    </Button>
                    <Button size="sm" icon={ThumbsDown} onClick={() => void decide(c, false)}>
                      Reject
                    </Button>
                  </div>
                )}
                {c.status === "ACTIVE" && (
                  <div className="border-t border-line p-3">
                    <Button
                      size="sm"
                      fullWidth
                      onClick={() =>
                        void api.merchant
                          .setCampaignStatus(c.id, "ENDED")
                          .then(() => loadCampaigns())
                      }
                    >
                      End campaign
                    </Button>
                  </div>
                )}
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SimulationProgress({ stage }: { stage: number }) {
  return (
    <Card className="p-4">
      <p className="mb-3 text-xs font-medium text-fg">Running experiment…</p>
      <ol className="space-y-2">
        {STAGES.map((s, i) => (
          <li key={s} className="flex items-center gap-2.5">
            <span
              className={cn(
                "grid h-4 w-4 shrink-0 place-items-center rounded-full border text-[9px] font-semibold",
                i < stage
                  ? "border-success bg-success text-white"
                  : i === stage
                    ? "border-brand bg-brand text-white"
                    : "border-line text-fg-faint",
              )}
              aria-hidden
            >
              {i < stage ? "✓" : i + 1}
            </span>
            <span
              className={cn(
                "text-xs",
                i <= stage ? "text-fg" : "text-fg-faint",
                i === stage && "font-medium",
              )}
            >
              {s}
            </span>
            {i === stage && (
              <span className="h-1 flex-1 overflow-hidden rounded-full bg-surface-muted">
                <span className="skeleton block h-full w-full" />
              </span>
            )}
          </li>
        ))}
      </ol>
    </Card>
  );
}

function ArmCard({
  title,
  subtitle,
  metrics,
  highlight,
}: {
  title: string;
  subtitle: string;
  metrics: Metrics;
  highlight?: boolean;
}) {
  return (
    <Card className={cn("p-4", highlight && "border-brand/40 bg-brand-soft/30")}>
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-fg">{title}</p>
          <p className="text-2xs text-fg-muted">{subtitle}</p>
        </div>
        <SyntheticBadge />
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
        <Figure label="Paid orders" value={String(metrics.paid_orders)} />
        <Figure label="Sessions" value={String(metrics.sessions)} />
        <Figure label="GMV" value={metrics.gmv_display} />
        <Figure label="AOV" value={metrics.aov_display} />
        <Figure label="Add-on revenue" value={metrics.cross_sell_revenue_display} />
        <Figure label="Failed payments" value={String(metrics.failed_payments)} />
      </dl>
    </Card>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-2xs uppercase tracking-wide text-fg-faint">{label}</dt>
      <dd className="tnum mt-0.5 text-xs font-medium text-fg">{value}</dd>
    </div>
  );
}

function MiniFigure({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-line px-2 py-1.5">
      <p className="text-2xs text-fg-faint">{label}</p>
      <p className="tnum mt-0.5 text-xs font-medium text-fg">{value}</p>
    </div>
  );
}

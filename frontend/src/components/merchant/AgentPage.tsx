import {
  Ban,
  CheckCircle2,
  Cpu,
  Lock,
  Save,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { PageHeader, SectionTitle } from "@/ui/data";
import { Callout, ErrorState, useToast } from "@/ui/feedback";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  Field,
  Input,
  Select,
  Skeleton,
  Toggle,
} from "@/ui/primitives";
import {
  ApiError,
  api,
  type AiStatus,
  type FailureMode,
  type Health,
  type MerchantSettings,
} from "@/lib/api";
import { cn, formatINR } from "@/lib/utils";

export function AgentPage({ health }: { health: Health | null }) {
  const [settings, setSettings] = useState<MerchantSettings | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [failure, setFailure] = useState<FailureMode | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const [s, a, f] = await Promise.all([
        api.merchant.settings(),
        api.merchant.ai(),
        api.merchant.failureInjection(),
      ]);
      setSettings(s);
      setAi(a);
      setFailure(f);
      setDraft({});
      setError(null);
    } catch (e) {
      setError((e as ApiError).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !settings) {
    return (
      <div className="p-4 sm:p-6">
        <ErrorState
          title="Unable to load agent configuration"
          message={error}
          onRetry={() => void load()}
        />
      </div>
    );
  }

  if (!settings || !ai) {
    return (
      <div className="space-y-4 p-4 sm:p-6">
        <Skeleton className="h-8 w-64" />
        <div className="grid gap-3 lg:grid-cols-3">
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
        <Skeleton className="h-72" />
      </div>
    );
  }

  const value = <K extends keyof typeof settings.settings>(key: K) =>
    (draft[key as string] ?? settings.settings[key]) as (typeof settings.settings)[K];

  const dirty = Object.keys(draft).length > 0;
  const activeProvider = ai.provider.active;

  async function save() {
    setSaving(true);
    try {
      const result = await api.merchant.updateSettings(draft);
      setSettings(result);
      setDraft({});
      if (result.clamped.length) {
        toast.push("warning", "Some values were clamped", result.clamped.join(" "));
      } else {
        toast.push("success", "Settings saved");
      }
    } catch (e) {
      toast.push("danger", "Could not save settings", (e as ApiError).message);
    } finally {
      setSaving(false);
    }
  }

  async function setFailureMode(patch: Partial<FailureMode>) {
    try {
      const result = await api.merchant.setFailureInjection(patch);
      setFailure({ outage: result.outage, verification_failure: result.verification_failure });
      toast.push("info", "Failure injection updated", result.note);
    } catch (e) {
      toast.push("danger", "Could not update failure injection", (e as ApiError).message);
    }
  }

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title="AI Agent"
        description="Control what your agent can see, recommend and do. The boundary below is architectural — it is enforced by the backend's tool registry, not by prompt instructions."
        actions={
          dirty && (
            <Button variant="primary" icon={Save} loading={saving} onClick={() => void save()}>
              Save changes
            </Button>
          )
        }
      />

      {/* ------------------------------------------------------- provider */}
      <div>
        <SectionTitle
          title="AI provider"
          description="Commerce never depends on a model being reachable."
        />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Card className="p-4 sm:col-span-2 lg:col-span-1">
            <div className="flex items-center gap-2">
              <div
                className={cn(
                  "grid h-9 w-9 place-items-center rounded-md",
                  activeProvider.deterministic ? "bg-warning-soft" : "bg-success-soft",
                )}
              >
                <Cpu
                  className={cn(
                    "h-4 w-4",
                    activeProvider.deterministic ? "text-warning" : "text-success",
                  )}
                  aria-hidden
                />
              </div>
              <div className="min-w-0">
                <p className="text-2xs uppercase tracking-wide text-fg-faint">Active</p>
                <p className="truncate text-sm font-semibold text-fg">
                  {activeProvider.deterministic ? "Deterministic planner" : activeProvider.provider}
                </p>
              </div>
            </div>
            <p className="mt-2 truncate font-mono text-2xs text-fg-muted">
              {activeProvider.model}
            </p>
            <div className="mt-2">
              <Badge tone={activeProvider.deterministic ? "warning" : "success"} dot>
                Operational
              </Badge>
            </div>
            {health?.llm.deterministic && (
              <p className="mt-2 text-2xs leading-relaxed text-fg-muted">
                No language model is reachable, so replies are generated by rule-based planning.
                Search, recommendations, cart and checkout are unaffected.
              </p>
            )}
          </Card>

          {Object.entries(ai.provider.providers)
            .filter(([name]) => name !== activeProvider.provider)
            .map(([name, p]) => (
              <Card key={name} className="p-4">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium capitalize text-fg">{name}</span>
                  <Badge tone={p.available ? "success" : "neutral"}>
                    {p.available ? "Available" : "Not configured"}
                  </Badge>
                </div>
                <p className="mt-1.5 truncate font-mono text-2xs text-fg-faint">{p.model ?? "—"}</p>
                {p.error && (
                  <p className="mt-2 line-clamp-3 text-2xs leading-relaxed text-fg-muted">
                    {p.error}
                  </p>
                )}
              </Card>
            ))}
        </div>
        <Callout tone={ai.provider.degraded ? "warning" : "info"} className="mt-3">
          {ai.provider.note}
        </Callout>
      </div>

      {/* --------------------------------------------------- capabilities */}
      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Agent capabilities"
            description="What the agent is permitted to do on a buyer's behalf."
          />
          <div className="divide-y divide-line border-t border-line">
            <CapabilityRow
              label="Catalog search"
              enabled
              detail="Reads the live catalog. Cannot invent products."
            />
            <CapabilityRow
              label="Recommendations"
              enabled
              detail="Ranked by the backend; the model only phrases the result."
            />
            <CapabilityRow
              label="Upsell"
              enabled={Boolean(value("upsell_enabled"))}
              detail="Compatible upgrades from curated catalog links."
            />
            <CapabilityRow
              label="Cross-sell"
              enabled={Boolean(value("cross_sell_enabled"))}
              detail="Frequently-bought-together items from the catalog graph."
            />
            <CapabilityRow
              label="Cart mutation"
              enabled
              detail="Can add and remove items. Cannot change any price."
            />
            <CapabilityRow
              label="Checkout preparation"
              enabled
              detail="Can produce a priced, policy-checked quote for a human to approve."
            />
          </div>
        </Card>

        <Card className="border-danger/25">
          <CardHeader
            title={
              <span className="flex items-center gap-1.5">
                <Lock className="h-3.5 w-3.5 text-danger" aria-hidden />
                Money permissions
              </span>
            }
            description="Blocked at the architecture level. These are not reachable from a model turn at all."
          />
          <div className="divide-y divide-line border-t border-line">
            {ai.tools.forbidden_capabilities.map((cap) => (
              <div key={cap.name} className="flex items-start gap-3 px-4 py-2.5">
                <Ban className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-fg">{cap.name}</span>
                    <Badge tone="danger">Blocked</Badge>
                  </div>
                  <p className="mt-0.5 text-2xs leading-relaxed text-fg-muted">{cap.reason}</p>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Callout tone="success" title="AI cannot spend your money">
        {ai.tools.money_boundary}
      </Callout>

      {/* --------------------------------------------------- policy center */}
      <div>
        <SectionTitle
          title="Purchase controls"
          description="The policy engine runs before every money action. Merchant values can only be stricter than the deployment ceiling."
        />
        <div className="grid gap-3 lg:grid-cols-2">
          <Card className="space-y-3 p-4">
            <Field
              label="Maximum order value (paise)"
              htmlFor="max-order"
              hint={`Currently ${formatINR(Number(value("max_order_value_paise")))} · deployment ceiling ${formatINR(settings.deployment_ceilings.max_order_value_paise)}`}
            >
              <Input
                id="max-order"
                type="number"
                min={100}
                step={100000}
                value={Number(value("max_order_value_paise"))}
                onChange={(e) =>
                  setDraft({ ...draft, max_order_value_paise: Number(e.target.value) })
                }
              />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field
                label="Maximum discount (%)"
                htmlFor="max-discount"
                hint={`Ceiling ${settings.deployment_ceilings.max_discount_percent}%`}
              >
                <Input
                  id="max-discount"
                  type="number"
                  min={0}
                  max={100}
                  value={Number(value("max_discount_percent"))}
                  onChange={(e) =>
                    setDraft({ ...draft, max_discount_percent: Number(e.target.value) })
                  }
                />
              </Field>
              <Field label="GST (%)" htmlFor="tax">
                <Input
                  id="tax"
                  type="number"
                  min={0}
                  max={50}
                  value={Number(value("tax_percent"))}
                  onChange={(e) => setDraft({ ...draft, tax_percent: Number(e.target.value) })}
                />
              </Field>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Max items per order" htmlFor="max-items">
                <Input
                  id="max-items"
                  type="number"
                  min={1}
                  value={Number(value("max_items_per_order"))}
                  onChange={(e) =>
                    setDraft({ ...draft, max_items_per_order: Number(e.target.value) })
                  }
                />
              </Field>
              <Field label="Max quantity per line" htmlFor="max-qty">
                <Input
                  id="max-qty"
                  type="number"
                  min={1}
                  value={Number(value("max_quantity_per_line"))}
                  onChange={(e) =>
                    setDraft({ ...draft, max_quantity_per_line: Number(e.target.value) })
                  }
                />
              </Field>
            </div>
          </Card>

          <Card className="space-y-2 p-4">
            <Toggle
              checked={Boolean(value("confirmation_required"))}
              onChange={(v) => setDraft({ ...draft, confirmation_required: v })}
              disabled={settings.deployment_ceilings.require_payment_confirmation}
              lockedReason={
                settings.deployment_ceilings.require_payment_confirmation
                  ? "Locked on — REQUIRE_PAYMENT_CONFIRMATION is enabled for this deployment."
                  : undefined
              }
              label="Require explicit payment confirmation"
              description="A human must approve the exact amount before any charge is created."
            />
            <Toggle
              checked={Boolean(value("upsell_enabled"))}
              onChange={(v) => setDraft({ ...draft, upsell_enabled: v })}
              label="Upsell enabled"
              description="Suggest compatible upgrades from curated catalog relationships."
            />
            <Toggle
              checked={Boolean(value("cross_sell_enabled"))}
              onChange={(v) => setDraft({ ...draft, cross_sell_enabled: v })}
              label="Cross-sell enabled"
              description="Suggest frequently-bought-together items from the catalog graph."
            />
            <Field
              label="AI provider preference"
              htmlFor="provider"
              hint="A model only changes how replies are worded. Products, prices, policy and payment outcomes come from the backend either way."
            >
              <Select
                id="provider"
                value={String(value("ai_provider_preference"))}
                onChange={(e) => setDraft({ ...draft, ai_provider_preference: e.target.value })}
              >
                <option value="auto">auto — Ollama, then Groq, Gemini, Claude, else deterministic</option>
                <option value="ollama">ollama — local install, free and private</option>
                <option value="groq">groq — free cloud tier, open-weight models</option>
                <option value="gemini">gemini — free cloud tier (Google AI Studio)</option>
                <option value="claude">claude — optional, paid API key</option>
                <option value="mock">mock — deterministic only (reproducible demos)</option>
              </Select>
            </Field>
          </Card>
        </div>
        <Callout tone="info" className="mt-3">
          {settings.deployment_ceilings.note}
        </Callout>
      </div>

      {/* -------------------------------------------------------- tooling */}
      <div>
        <SectionTitle
          title="Tool registry"
          description="The complete surface the model can act through. Anything not listed here is unreachable."
        />
        <Card>
          <div className="grid gap-px bg-line sm:grid-cols-2 lg:grid-cols-3">
            {ai.tools.allowed_tools.map((tool) => (
              <div key={tool.name} className="bg-surface p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-xs text-fg">{tool.name}</span>
                  <Badge
                    tone={
                      tool.permission === "read"
                        ? "neutral"
                        : tool.permission === "mutate_cart"
                          ? "warning"
                          : "brand"
                    }
                  >
                    {tool.permission.replace("_", " ")}
                  </Badge>
                </div>
                <p className="mt-1 text-2xs leading-relaxed text-fg-muted">{tool.description}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* --------------------------------------------- failure injection */}
      {failure && (
        <div>
          <SectionTitle
            title="Failure injection"
            description="Demonstrate graceful degradation against the real code path."
          />
          <Card className="space-y-2 p-4">
            <Toggle
              checked={failure.outage}
              onChange={(v) => void setFailureMode({ outage: v })}
              label="Payment provider outage"
              description="Every provider call raises 'unavailable'. Orders stay unpaid and the buyer is told the state is unknown."
            />
            <Toggle
              checked={failure.verification_failure}
              onChange={(v) => void setFailureMode({ verification_failure: v })}
              label="Force verification failure"
              description="Verification fails even for a valid signature — a provider that stops agreeing with a callback the browser already accepted."
            />
            <Callout tone="warning" className="mt-1">
              Failure injection can only <strong>add</strong> failures. There is deliberately no
              switch that fakes a successful payment.
            </Callout>
          </Card>
        </div>
      )}
    </div>
  );
}

function CapabilityRow({
  label,
  enabled,
  detail,
}: {
  label: string;
  enabled: boolean;
  detail: string;
}) {
  return (
    <div className="flex items-start gap-3 px-4 py-2.5">
      {enabled ? (
        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" aria-hidden />
      ) : (
        <Ban className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fg-faint" aria-hidden />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-fg">{label}</span>
          <Badge tone={enabled ? "success" : "neutral"}>{enabled ? "Enabled" : "Disabled"}</Badge>
        </div>
        <p className="mt-0.5 text-2xs leading-relaxed text-fg-muted">{detail}</p>
      </div>
    </div>
  );
}

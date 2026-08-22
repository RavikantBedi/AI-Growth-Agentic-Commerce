/**
 * Typed API client.
 *
 * Note what this client never sends: a price, a line total, an order total, or
 * an order/payment status. It sends ids and quantities; every rupee figure in
 * the UI is a value the backend computed and returned.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: unknown;

  constructor(message: string, status: number, code = "error", detail: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }

  /** True when the user can sensibly try the same thing again. */
  get retryable(): boolean {
    const d = this.detail as { retryable?: boolean } | null;
    return d?.retryable === true || this.status >= 500;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError(
      "Could not reach the server. Check that the backend is running on port 8000.",
      0,
      "network_error",
    );
  }

  const text = await response.text();
  const body = text ? safeJson(text) : null;

  if (!response.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    if (detail && typeof detail === "object") {
      const d = detail as { error?: string; message?: string; detail?: unknown };
      throw new ApiError(
        d.message ?? `Request failed (${response.status})`,
        response.status,
        d.error ?? "error",
        d.detail ?? null,
      );
    }
    const flat = body as { error?: string; message?: string } | null;
    throw new ApiError(
      flat?.message ?? (typeof detail === "string" ? detail : `Request failed (${response.status})`),
      response.status,
      flat?.error ?? "error",
    );
  }
  return body as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

const get = <T,>(path: string) => request<T>(path);
const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const put = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });
const del = <T,>(path: string) => request<T>(path, { method: "DELETE" });

export const api = {
  health: () => get<Health>("/api/health"),
  manifest: () => get<Record<string, unknown>>("/.well-known/agent-commerce.json"),

  buyer: {
    startSession: (label = "buyer") =>
      post<SessionStart>("/api/buyer/session", {
        actor_type: "human",
        actor_label: label,
        channel: "web",
      }),
    chat: (sessionId: string, message: string) =>
      post<ChatResponse>("/api/buyer/chat", { session_id: sessionId, message }),
    history: (sessionId: string) =>
      get<{ messages: HistoryMessage[] }>(`/api/buyer/history/${sessionId}`),
    cart: (sessionId: string) => get<Cart>(`/api/buyer/cart/${sessionId}`),
    addToCart: (sessionId: string, productId: string, quantity = 1, source = "direct") =>
      post<Cart>("/api/buyer/cart/add", {
        session_id: sessionId,
        product_id: productId,
        quantity,
        source,
      }),
    setQuantity: (sessionId: string, productId: string, quantity: number) =>
      post<Cart>("/api/buyer/cart/quantity", {
        session_id: sessionId,
        product_id: productId,
        quantity,
      }),
    removeFromCart: (sessionId: string, productId: string) =>
      post<Cart>("/api/buyer/cart/remove", { session_id: sessionId, product_id: productId }),
    clearCart: (sessionId: string) => post<Cart>(`/api/buyer/cart/clear/${sessionId}`),
    products: (category?: string) =>
      get<{ products: Product[]; total: number; categories: CategorySummary[] }>(
        `/api/buyer/products${category ? `?category=${encodeURIComponent(category)}` : ""}`,
      ),
  },

  payments: {
    config: () => get<PaymentConfig>("/api/payments/config"),
    health: () => get<Record<string, unknown>>("/api/payments/health"),
    prepare: (sessionId: string) =>
      post<Quote>("/api/payments/prepare", { session_id: sessionId }),
    confirm: (quoteId: string, confirmedBy: string, idempotencyKey?: string) =>
      post<PaymentOrder>("/api/payments/confirm", {
        quote_id: quoteId,
        confirmed: true,
        confirmed_by: confirmedBy,
        ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
      }),
    verify: (orderId: string, paymentId: string, signature: string) =>
      post<PaymentResult>("/api/payments/verify", {
        razorpay_order_id: orderId,
        razorpay_payment_id: paymentId,
        razorpay_signature: signature,
      }),
    reportFailure: (orderId: string, reason: string) =>
      post<PaymentResult>("/api/payments/failed", {
        razorpay_order_id: orderId,
        reason,
      }),
    reconcile: (orderId: string) => post<PaymentResult>(`/api/payments/reconcile/${orderId}`),
    sandboxPay: (providerOrderId: string, outcome: SandboxOutcome) =>
      post<SandboxAttempt>("/api/payments/sandbox/pay", {
        provider_order_id: providerOrderId,
        outcome,
      }),
    order: (orderId: string) => get<OrderDetail>(`/api/payments/order/${orderId}`),
  },

  merchant: {
    overview: () => get<Overview>("/api/merchant/overview"),
    activity: (limit = 25) =>
      get<{ sessions: ActivitySession[] }>(`/api/merchant/activity?limit=${limit}`),
    metrics: (scope: MetricScope = "all") =>
      get<Metrics>(`/api/merchant/metrics?scope=${scope}`),
    settings: () => get<MerchantSettings>("/api/merchant/settings"),
    updateSettings: (body: Partial<MerchantSettingsValues>) =>
      put<MerchantSettings & { clamped: string[] }>("/api/merchant/settings", body),
    products: () =>
      get<{ products: Product[]; total: number; categories: CategorySummary[]; brands: string[] }>(
        "/api/merchant/products?limit=500",
      ),
    createProduct: (body: ProductInput) => post<Product>("/api/merchant/products", body),
    updateProduct: (id: string, body: Partial<ProductInput>) =>
      put<Product>(`/api/merchant/products/${id}`, body),
    deleteProduct: (id: string) => del<{ product_id: string }>(`/api/merchant/products/${id}`),
    seed: (reset = false) => post<SeedResult>(`/api/merchant/seed?reset=${reset}`),
    campaigns: () => get<{ campaigns: Campaign[] }>("/api/merchant/campaigns"),
    createCampaign: (body: CampaignInput) =>
      post<Campaign & { notes: string[] }>("/api/merchant/campaigns", body),
    recommendCampaign: () => post<CampaignProposal>("/api/merchant/campaigns/recommend"),
    approveCampaign: (id: string, approver: string) =>
      post<Campaign>(`/api/merchant/campaigns/${id}/approve`, { approver }),
    rejectCampaign: (id: string, approver: string, reason: string) =>
      post<Campaign>(`/api/merchant/campaigns/${id}/reject`, { approver, reason }),
    setCampaignStatus: (id: string, status: CampaignStatus, actor = "merchant") =>
      put<Campaign>(`/api/merchant/campaigns/${id}/status`, { status, actor }),
    ai: () => get<AiStatus>("/api/merchant/ai"),
    simulate: (sessionsPerArm: number, seed = 1337) =>
      post<SimulationResult>("/api/merchant/simulate", {
        sessions_per_arm: sessionsPerArm,
        seed,
      }),
    experiments: () => get<{ experiments: ExperimentRun[] }>("/api/merchant/experiments"),
    failureInjection: () => get<FailureMode>("/api/merchant/failure-injection"),
    setFailureInjection: (body: Partial<FailureMode>) =>
      post<FailureMode & { note: string }>("/api/merchant/failure-injection", body),
  },

  audit: {
    events: (params: Record<string, string | number | boolean | undefined>) => {
      const q = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== "") q.set(k, String(v));
      }
      return get<{ events: AuditEvent[]; count: number }>(`/api/audit/events?${q}`);
    },
    actions: () => get<{ actions: string[]; decisions: string[] }>("/api/audit/actions"),
    story: (orderId: string) => get<AuditStory>(`/api/audit/story/${orderId}`),
    session: (sessionId: string) =>
      get<{ events: AuditEvent[]; count: number }>(`/api/audit/session/${sessionId}`),
  },
};

/* -------------------------------------------------------------------------- */
/* Types                                                                       */
/* -------------------------------------------------------------------------- */

export type MetricScope = "all" | "live" | "synthetic";
export type SandboxOutcome =
  | "success"
  | "failure"
  | "tampered_signature"
  | "authorized_only"
  | "provider_outage";
export type CampaignStatus =
  | "DRAFT"
  | "PENDING_APPROVAL"
  | "ACTIVE"
  | "PAUSED"
  | "ENDED"
  | "REJECTED";

export interface Health {
  status: string;
  app_env: string;
  test_mode: boolean;
  payment_provider: { name: string; label: string; simulated: boolean };
  llm: { provider: string; model: string; deterministic: boolean };
  guardrails: {
    max_order_value_paise: number;
    max_discount_percent: number;
    require_payment_confirmation: boolean;
  };
}

export interface Product {
  id: string;
  sku: string;
  name: string;
  description: string;
  category: string;
  subcategory: string;
  brand: string;
  price_paise: number;
  price_display: string;
  currency: string;
  inventory: number;
  in_stock: boolean;
  attributes: Record<string, string>;
  tags: string[];
  images: string[];
  active: boolean;
  related_products?: string[];
  frequently_bought_together?: string[];
  compatible_products?: string[];
  score?: number;
  signals?: Record<string, number>;
  why?: string;
}

export interface ProductInput {
  sku: string;
  name: string;
  description?: string;
  category?: string;
  subcategory?: string;
  brand?: string;
  price_paise: number;
  inventory?: number;
  tags?: string[];
  attributes?: Record<string, string>;
  active?: boolean;
}

export interface CategorySummary {
  category: string;
  product_count: number;
  min_price_paise: number;
  max_price_paise: number;
  price_range_display: string;
}

export interface CartLine {
  product_id: string;
  sku: string;
  name: string;
  unit_price_paise: number;
  unit_price_display: string;
  quantity: number;
  line_total_paise: number;
  line_total_display: string;
  source: "direct" | "upsell" | "cross_sell";
  category: string;
  brand?: string;
  images?: string[];
  inventory?: number;
  in_stock?: boolean;
  active?: boolean;
}

export interface Cart {
  order_id: string | null;
  session_id: string;
  status: string | null;
  lines: CartLine[];
  item_count: number;
  subtotal_paise: number;
  subtotal_display: string;
  discount_paise: number;
  discount_display: string;
  discount_label: string;
  campaign_id: string | null;
  tax_paise: number;
  tax_display: string;
  tax_percent: number;
  shipping_paise: number;
  total_paise: number;
  total_display: string;
  currency: string;
  fingerprint: string;
  upsell_paise: number;
  cross_sell_paise: number;
}

export interface UpsellSuggestion {
  product_id: string;
  sku: string;
  name: string;
  brand: string;
  category: string;
  price_paise: number;
  price_display: string;
  images: string[];
  inventory: number;
  kind: "upsell" | "cross_sell";
  anchor_product_id: string;
  anchor_name: string;
  reason: string;
  incremental_paise: number;
  incremental_display: string;
  new_subtotal_paise: number;
  new_subtotal_display: string;
  new_total_paise: number;
  new_total_display: string;
}

export interface InjectionScan {
  detected: boolean;
  patterns: string[];
  samples: string[];
  source: string;
}

export interface ChatResponse {
  session_id: string;
  message: string;
  intent: string;
  reason: string;
  products: Product[];
  recommendations: Product[];
  upsells: UpsellSuggestion[];
  upsell_bounds: Record<string, number | boolean>;
  upsell_rejected: { product_id?: string; name?: string; reason: string }[];
  cart: Cart | null;
  checkout: Quote | null;
  checkout_error?: { code: string; detail: unknown };
  requirements: {
    raw_query: string;
    max_price_paise: number | null;
    max_price_display: string | null;
    category: string | null;
    use_case_tags: string[];
    keywords: string[];
  };
  ai: {
    provider: string;
    model: string;
    degraded: boolean;
    degraded_reason: string;
    latency_ms: number;
    candidates_considered: number;
  };
  security: {
    shopper_message_injection: InjectionScan;
    catalog_injection: InjectionScan;
  };
  latency_ms: number;
}

export interface HistoryMessage {
  role: "user" | "agent";
  content: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface SessionStart {
  session_id: string;
  merchant: { name: string; description: string; currency: string };
  ai: AiProviderStatus;
  greeting: string;
}

export interface PolicyCheck {
  rule: string;
  passed: boolean;
  message: string;
  limit: unknown;
  observed: unknown;
}

export interface PolicyResult {
  allowed: boolean;
  requires_confirmation: boolean;
  summary: string;
  checks: PolicyCheck[];
  violations: PolicyCheck[];
  policy: Record<string, unknown>;
}

export interface Quote {
  quote_id: string;
  order_id: string;
  session_id: string;
  status: string;
  cart: Cart;
  policy_result: PolicyResult;
  explanation: string;
  requires_confirmation: boolean;
  expires_at: string;
  payment_provider: PaymentConfig;
  test_mode: boolean;
  notice: string;
  agent_note?: string;
}

export interface PaymentConfig {
  provider: string;
  test_mode: boolean;
  label: string;
  simulated?: boolean;
  warning?: string;
  key_id?: string;
  checkout_script?: string;
  banner?: string;
  failure_injection?: FailureMode;
}

export interface PaymentOrder {
  order_id: string;
  transaction_id: string;
  provider: string;
  provider_order_id: string;
  amount_paise: number;
  amount_display: string;
  currency: string;
  status: string;
  payment_status: string;
  test_mode: boolean;
  provider_config: PaymentConfig;
  notice: string;
  replayed: boolean;
}

export interface SandboxAttempt {
  provider_order_id: string;
  provider_payment_id: string;
  signature: string;
  outcome: string;
  simulated: boolean;
  next_step: string;
}

export interface PaymentResult {
  order_id: string;
  order_status: string;
  paid: boolean;
  payment_status: string;
  verification_status: string;
  verified: boolean;
  provider: string;
  provider_order_id: string;
  provider_payment_id: string | null;
  amount_paise: number;
  amount_display: string;
  message: string;
  replayed: boolean;
  user_message: string;
  retry_available: boolean;
  test_mode: boolean;
}

export interface TransactionView {
  transaction_id: string;
  provider: string;
  provider_order_id: string;
  provider_payment_id: string | null;
  amount_paise: number;
  amount_display: string;
  currency: string;
  status: string;
  verification_status: string;
  signature_present: boolean;
  failure_reason: string | null;
  is_test_mode: boolean;
  provider_meta: Record<string, unknown>;
  created_at: string;
}

export interface OrderDetail {
  order_id: string;
  session_id: string;
  status: string;
  paid: boolean;
  items: CartLine[];
  subtotal_display: string;
  discount_display: string;
  tax_display: string;
  total_paise: number;
  total_display: string;
  currency: string;
  payment_provider: string | null;
  payment_order_id: string | null;
  payment_id: string | null;
  is_synthetic: boolean;
  created_at: string;
  updated_at: string;
  transactions: TransactionView[];
}

export interface Metrics {
  scope: string;
  is_synthetic: boolean;
  label: string;
  sessions: number;
  converting_sessions: number;
  orders_created: number;
  paid_orders: number;
  failed_payments: number;
  conversion_rate_percent: number;
  gmv_paise: number;
  gmv_display: string;
  aov_paise: number;
  aov_display: string;
  revenue_per_session_paise: number;
  revenue_per_session_display: string;
  upsell_revenue_paise: number;
  upsell_revenue_display: string;
  cross_sell_revenue_paise: number;
  cross_sell_revenue_display: string;
  discount_given_display: string;
  upsell_suggested_sessions: number;
  upsell_accepted_sessions: number;
  addon_accepted_sessions: number;
  upsell_acceptance_rate_percent: number;
  cross_sell_acceptance_rate_percent: number;
  addon_acceptance_rate_percent: number;
  carts_with_items: number;
  abandoned_carts: number;
  cart_abandonment_rate_percent: number;
  payment_failure_rate_percent: number;
}

export interface Overview {
  live: Metrics;
  synthetic: Metrics;
  combined: Metrics;
  recent_paid_orders: {
    order_id: string;
    total_display: string;
    total_paise: number;
    is_synthetic: boolean;
    payment_id: string | null;
    updated_at: string;
  }[];
  disclaimer: string;
}

export interface ActivitySession {
  session_id: string;
  actor_type: string;
  actor_label: string;
  channel: string;
  variant: string;
  is_synthetic: boolean;
  created_at: string;
  last_intent: string;
  order_id: string | null;
  order_status: string | null;
  items: number;
  total_paise: number;
  total_display: string;
  payment_id: string | null;
}

export interface MerchantSettingsValues {
  name: string;
  description: string;
  support_email: string;
  max_order_value_paise: number;
  max_discount_percent: number;
  max_items_per_order: number;
  max_quantity_per_line: number;
  confirmation_required: boolean;
  upsell_enabled: boolean;
  cross_sell_enabled: boolean;
  tax_percent: number;
  ai_provider_preference: "auto" | "ollama" | "mock" | "claude";
}

export interface MerchantSettings {
  id: string;
  name: string;
  description: string;
  support_email: string;
  currency: string;
  settings: MerchantSettingsValues & { max_order_value_display: string };
  effective_policy: Record<string, unknown>;
  deployment_ceilings: {
    max_order_value_paise: number;
    max_discount_percent: number;
    require_payment_confirmation: boolean;
    note: string;
  };
  policies: Record<string, string>;
}

export interface Campaign {
  id: string;
  name: string;
  description: string;
  target_segment: string;
  product_ids: string[];
  discount_percent: number;
  starts_at: string | null;
  ends_at: string | null;
  budget_paise: number;
  budget_display: string;
  spent_paise: number;
  spent_display: string;
  remaining_paise: number;
  max_discount_paise_per_order: number;
  status: CampaignStatus;
  created_by: string;
  approved_by: string | null;
  approved_at: string | null;
  ai_rationale: string;
  created_at: string;
  notes?: string[];
}

export interface CampaignProposal extends Campaign {
  notes: string[];
  requested_discount_percent: number;
  applied_discount_percent: number;
  requires_merchant_approval: boolean;
  note: string;
}

export interface CampaignInput {
  name: string;
  description?: string;
  target_segment?: string;
  product_ids?: string[];
  discount_percent: number;
  budget_paise?: number;
  max_discount_paise_per_order?: number;
}

export interface AiProviderStatus {
  configured: string;
  active: { provider: string; model: string; deterministic: boolean };
  fallback: { provider: string; model: string; deterministic: boolean };
  providers: Record<
    string,
    { provider: string; available: boolean; model?: string; error?: string | null }
  >;
  degraded: boolean;
  note: string;
}

export interface ToolSpec {
  name: string;
  description: string;
  permission: string;
  parameters: Record<string, string>;
}

export interface AiStatus {
  provider: AiProviderStatus;
  tools: {
    allowed_tools: ToolSpec[];
    forbidden_capabilities: { name: string; reason: string }[];
    permissions: Record<string, string>;
    money_boundary: string;
  };
}

export interface FailureMode {
  outage: boolean;
  verification_failure: boolean;
}

export interface AuditEvent {
  id: number;
  created_at: string;
  action: string;
  actor: string;
  actor_type: string;
  session_id: string | null;
  order_id: string | null;
  request_id: string | null;
  reason: string;
  input: Record<string, unknown>;
  decision: string;
  policy_result: Record<string, unknown>;
  payment_reference: string | null;
  amount_paise: number | null;
  amount_display: string | null;
  status: string;
  is_synthetic: boolean;
}

export interface AuditStory {
  order_id: string;
  session_id: string;
  status: string;
  is_synthetic: boolean;
  narrative: {
    what_was_requested: string;
    what_the_agent_selected: string;
    what_was_suggested: string;
    what_is_being_bought: {
      name: string;
      sku: string;
      quantity: number;
      unit_price_display: string;
      line_total_display: string;
      how_it_entered_the_cart: string;
    }[];
    how_much: Record<string, string>;
    which_policy: PolicyResult | Record<string, never>;
    policy_verdict: string;
    who_approved: {
      actor: string | null;
      actor_type: string | null;
      decision: string | null;
      at: string | null;
      detail: string;
    };
    which_provider: {
      provider: string | null;
      provider_order_id: string | null;
      created: string | null;
      test_mode: boolean;
    };
    what_was_the_result: string;
    was_it_verified: {
      verification_status: string;
      payment_status: string | null;
      signature_checked: boolean;
      order_is_paid: boolean;
      statement: string;
    };
  };
  approved_quote: {
    quote_id: string;
    total_display: string;
    explanation: string;
    cart_fingerprint: string;
    confirmed_by: string | null;
    confirmed_at: string | null;
    policy_result: PolicyResult;
  } | null;
  transactions: TransactionView[];
  timeline: AuditEvent[];
  event_count: number;
}

export interface ComparisonDelta {
  baseline: number;
  ai_assisted: number;
  absolute_change: number;
  percent_change: number | null;
}

export interface SimulationResult {
  experiment_id: string;
  label: string;
  sessions_per_arm: number;
  seed: number;
  duration_seconds: number;
  baseline: Metrics;
  ai_assisted: Metrics;
  comparison: Record<string, ComparisonDelta | Record<string, string> | string>;
  assumptions: Record<string, number | string>;
  payment_note: string;
  label_warning: string;
}

export interface ExperimentRun {
  id: string;
  label: string;
  sessions_per_arm: number;
  created_at: string;
  results: SimulationResult;
  is_synthetic: boolean;
}

export interface SeedResult {
  merchant: string;
  products_created: number;
  products_updated: number;
  total_products: number;
  reset: boolean;
  note: string;
}

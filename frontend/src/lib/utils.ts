import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Format paise as INR with Indian digit grouping.
 *
 * The backend already returns a `*_display` string for every amount it owns;
 * this exists for the few places the UI derives a figure locally (a delta, a
 * projected total in a preview). Anything the user will be charged always uses
 * the backend's string.
 */
export function formatINR(paise: number): string {
  const negative = paise < 0;
  const whole = Math.floor(Math.abs(paise) / 100);
  const frac = Math.abs(paise) % 100;
  const s = String(whole);
  let grouped = s;
  if (s.length > 3) {
    const tail = s.slice(-3);
    let head = s.slice(0, -3);
    const groups: string[] = [];
    while (head.length > 2) {
      groups.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head) groups.unshift(head);
    grouped = [...groups, tail].join(",");
  }
  return `${negative ? "-" : ""}₹${grouped}.${String(frac).padStart(2, "0")}`;
}

export function formatPercent(value: number, digits = 1): string {
  return `${value.toFixed(digits)}%`;
}

export function relativeTime(iso: string): string {
  const then = new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime();
  const seconds = Math.floor((Date.now() - then) / 1000);
  if (Number.isNaN(seconds)) return iso;
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function formatTime(iso: string): string {
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Stable colour + label for an order status. */
export function orderStatusStyle(status: string | null): { className: string; label: string } {
  switch (status) {
    case "PAID":
      return { className: "bg-ok/15 text-ok border-ok/30", label: "Paid" };
    case "PAYMENT_FAILED":
      return { className: "bg-danger/15 text-danger border-danger/30", label: "Payment failed" };
    case "PAYMENT_PENDING":
      return { className: "bg-warn/15 text-warn border-warn/30", label: "Payment pending" };
    case "CHECKOUT_PENDING":
      return { className: "bg-brand/15 text-brand-bright border-brand/30", label: "Awaiting approval" };
    case "CART":
      return { className: "bg-ink-faint/15 text-ink-muted border-ink-faint/30", label: "Cart" };
    case "CANCELLED":
      return { className: "bg-ink-faint/15 text-ink-faint border-ink-faint/30", label: "Cancelled" };
    case "REFUNDED":
      return { className: "bg-brand/15 text-brand-bright border-brand/30", label: "Refunded" };
    default:
      return { className: "bg-ink-faint/15 text-ink-muted border-ink-faint/30", label: status ?? "—" };
  }
}

export function paymentStatusStyle(status: string): string {
  if (status === "CAPTURED") return "bg-ok/15 text-ok border-ok/30";
  if (status === "AUTHORIZED") return "bg-warn/15 text-warn border-warn/30";
  if (status === "FAILED" || status === "VERIFICATION_FAILED")
    return "bg-danger/15 text-danger border-danger/30";
  return "bg-ink-faint/15 text-ink-muted border-ink-faint/30";
}

/** Colour for an audit decision. */
export function decisionStyle(decision: string): string {
  if (decision === "ALLOWED") return "bg-ok/15 text-ok border-ok/30";
  if (decision === "REJECTED") return "bg-danger/15 text-danger border-danger/30";
  return "bg-brand/15 text-brand-bright border-brand/30";
}

/**
 * Deterministic gradient for a product with no image, derived from its SKU so
 * a given product always looks the same.
 */
export function productGradient(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) | 0;
  const hue = Math.abs(hash) % 360;
  return `linear-gradient(135deg, hsl(${hue} 55% 28%), hsl(${(hue + 48) % 360} 50% 16%))`;
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter((w) => /[a-z0-9]/i.test(w))
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

/** A short id for display, e.g. ord_9f3a…c21d */
export function shortId(id: string | null | undefined, keep = 6): string {
  if (!id) return "—";
  if (id.length <= keep * 2 + 1) return id;
  return `${id.slice(0, keep + 4)}…${id.slice(-4)}`;
}
